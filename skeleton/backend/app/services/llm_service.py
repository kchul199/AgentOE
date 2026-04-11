"""
LLM Service — Groq Llama 4 Scout / 3.3 70B
스트리밍 응답, Filler Audio 전략, Circuit Breaker 적용
"""
import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from app.domain.circuit_breaker import CircuitBreakerConfig, get_circuit_breaker

logger = logging.getLogger(__name__)

FILLER_THRESHOLD_MS = 500   # 이 시간 초과 시 Filler Audio 트리거
FILLER_PHRASES = [
    "잠시만요,",
    "확인해 드리겠습니다,",
    "네, 알겠습니다,",
]

LLM_CB_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    recovery_timeout=30.0,
    half_open_max_calls=2,
    success_threshold=2,
)


@dataclass
class LLMChunk:
    text: str
    is_final: bool
    is_filler: bool = False


@dataclass
class LLMResult:
    full_text: str
    duration_ms: float
    model: str
    tokens_used: int = 0
    filler_triggered: bool = False


class LLMService:
    """
    Groq Llama 4 Scout LLM 서비스.
    - 스트리밍 응답 지원
    - 500ms 초과 시 Filler Audio 청크 먼저 방출
    - Circuit Breaker: Llama 4 Scout → Llama 3.3 70B Fallback
    """

    def __init__(self) -> None:
        self._cb_primary = get_circuit_breaker("groq-llm-primary", LLM_CB_CONFIG)
        self._cb_fallback = get_circuit_breaker("groq-llm-fallback", LLM_CB_CONFIG)
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from groq import AsyncGroq
                from app.core.config import settings
                self._client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            except ImportError:
                raise RuntimeError("groq package not installed")
        return self._client

    def _build_messages(
        self, user_text: str, history: list[dict], system_prompt: str | None = None
    ) -> list[dict]:
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.extend(history[-10:])  # 최근 10턴만 컨텍스트 유지
        messages.append({"role": "user", "content": user_text})
        return messages

    async def stream(
        self,
        user_text: str,
        history: list[dict] | None = None,
        system_prompt: str | None = None,
        use_fallback: bool = False,
    ) -> AsyncIterator[LLMChunk]:
        """스트리밍 LLM 응답 생성. Filler Audio 자동 트리거."""
        from app.core.config import settings

        messages = self._build_messages(user_text, history or [], system_prompt)
        cb = self._cb_fallback if use_fallback else self._cb_primary
        model = settings.GROQ_LLM_FALLBACK_MODEL if use_fallback else settings.GROQ_LLM_MODEL

        start = time.monotonic()
        first_chunk_sent = False
        filler_triggered = False
        full_text = []

        async def _create_stream():
            client = self._get_client()
            return await client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                max_tokens=512,
                temperature=0.7,
            )

        try:
            stream = await cb.call(_create_stream)
        except Exception as exc:
            if not use_fallback:
                logger.warning("Primary LLM failed, trying fallback: %s", exc)
                async for chunk in self.stream(user_text, history, system_prompt, use_fallback=True):
                    yield chunk
                return
            raise

        async for chunk in stream:
            if not first_chunk_sent:
                elapsed_ms = (time.monotonic() - start) * 1000
                if elapsed_ms > FILLER_THRESHOLD_MS and not filler_triggered:
                    filler_triggered = True
                    import random
                    filler = random.choice(FILLER_PHRASES)
                    yield LLMChunk(text=filler, is_final=False, is_filler=True)
                first_chunk_sent = True

            delta = chunk.choices[0].delta.content or ""
            if delta:
                full_text.append(delta)
                yield LLMChunk(text=delta, is_final=False)

        yield LLMChunk(text="".join(full_text), is_final=True)

    async def complete(
        self,
        user_text: str,
        history: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> LLMResult:
        """비스트리밍 완성 (배치 처리용)"""
        from app.core.config import settings
        start = time.monotonic()
        full_text = []
        filler_triggered = False

        async for chunk in self.stream(user_text, history, system_prompt):
            if chunk.is_filler:
                filler_triggered = True
            elif chunk.is_final:
                full_text = [chunk.text]
            else:
                full_text.append(chunk.text)

        return LLMResult(
            full_text="".join(full_text),
            duration_ms=(time.monotonic() - start) * 1000,
            model=settings.GROQ_LLM_MODEL,
            filler_triggered=filler_triggered,
        )
