"""
LLM Service — Groq Llama 4 Scout / 3.3 70B
스트리밍 응답, Filler Audio 전략, Circuit Breaker 적용

쿼터 정책 (Track 2-d):
  호출 진입 시 enforce_quota(tenant_id) 로 일일 LLM 쿼터를 확인한다.
  응답 완료 후 commit_usage(tenant_id, tokens=..., cost_cents=...) 로 누적.
  QuotaExceededError 는 상위(에이전틱 노드) 로 전파해 fallback 시나리오 처리.
"""

import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from app.core.metrics import record_llm_usage
from app.core.quota import commit_usage, enforce_quota
from app.core.timeouts import SVC_LLM, with_timeout
from app.domain.circuit_breaker import (
    CircuitBreakerOpenError,
    get_circuit_breaker,
    make_service_config,
)

logger = logging.getLogger(__name__)

FILLER_THRESHOLD_MS = 500  # 이 시간 초과 시 Filler Audio 트리거
FILLER_PHRASES = [
    "잠시만요,",
    "확인해 드리겠습니다,",
    "네, 알겠습니다,",
]

# 간이 모델별 가격표 (USD cents per 1K tokens, prompt/completion 평균).
# 정확한 과금은 별도 settings.LLM_PRICING 으로 교체 가능하도록 구성.
_DEFAULT_PRICE_CENTS_PER_1K: dict[str, float] = {
    # Groq 무료 구간 기준 — 상용 도입 시 실제 단가로 override
    "llama-3.3-70b-versatile": 0.06,
    "llama-4-scout-17b-16e-instruct": 0.03,
}


def _estimate_cost_cents(model: str, total_tokens: int) -> float:
    rate = _DEFAULT_PRICE_CENTS_PER_1K.get(model, 0.05)
    return (total_tokens / 1000.0) * rate


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

    CB 파라미터는 settings.CB_LLM_* 에서 읽으며, make_service_config()가 적용합니다.

    CB 참고사항:
      stream()은 초기 스트림 연결(create 호출)만 CB로 감쌉니다.
      실제 스트리밍 청크 수신은 CB 범위 밖입니다. 이는 의도된 설계로,
      스트리밍 중 단절은 세션 레벨에서 처리합니다.

      CircuitBreakerOpenError는 Fallback 전환 대상이 아닙니다.
      Fallback(use_fallback=True)은 실제 Groq API 실패 시에만 작동합니다.
    """

    def __init__(self) -> None:
        self._cb_primary = get_circuit_breaker(
            "groq-llm-primary", make_service_config("groq-llm-primary")
        )
        self._cb_fallback = get_circuit_breaker(
            "groq-llm-fallback", make_service_config("groq-llm-fallback")
        )
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from groq import AsyncGroq

                from app.core.config import settings

                self._client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            except ImportError:
                raise RuntimeError("groq package not installed") from None
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
        *,
        tenant_id: str | None = None,
        tenant_cfg: dict | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """스트리밍 LLM 응답 생성. Filler Audio 자동 트리거.

        CB는 초기 스트림 연결(create)만 감쌉니다.
        CircuitBreakerOpenError: Fallback을 시도하지 않고 즉시 전파합니다.
        그 외 예외 (API 오류, 네트워크 실패 등): Primary → Fallback 자동 전환.

        tenant_id 가 주어지면 호출 진입 시 일일 LLM 쿼터를 체크하고, 완료 후
        토큰/비용 사용량을 commit 한다. 쿼터 초과 시 QuotaExceededError 전파.
        """
        from app.core.config import settings

        messages = self._build_messages(user_text, history or [], system_prompt)
        cb = self._cb_fallback if use_fallback else self._cb_primary
        model = settings.GROQ_LLM_FALLBACK_MODEL if use_fallback else settings.GROQ_LLM_MODEL

        # ── 쿼터 선검증 (재귀 fallback 호출은 이미 통과했으므로 생략) ──────
        if tenant_id and not use_fallback:
            await enforce_quota(tenant_id, tenant_cfg)

        start = time.monotonic()
        first_chunk_sent = False
        filler_triggered = False
        full_text = []
        tokens_used = 0

        # ── 스트림 연결 획득 (CB 범위 + 절대 timeout) ────────────────────
        # Groq 미응답 시 LLM_TIMEOUT_SECONDS 내에 실패로 간주되어 CB가 즉시 OPEN.
        try:
            async with cb:
                client = self._get_client()
                stream_obj = await with_timeout(
                    client.chat.completions.create(
                        model=model,
                        messages=messages,
                        stream=True,
                        max_tokens=512,
                        temperature=0.7,
                    ),
                    service=SVC_LLM,
                )
        except CircuitBreakerOpenError:
            # CB OPEN: Fallback을 시도하지 않음 — 오케스트레이터가 처리
            raise
        except Exception as exc:
            # 실제 API 실패: Primary에서만 Fallback 전환
            # excluded_exceptions(RuntimeError)는 CB failure_count에 포함 안 됨
            if not use_fallback:
                logger.warning("Primary LLM failed, trying fallback: %s", exc)
                async for chunk in self.stream(
                    user_text,
                    history,
                    system_prompt,
                    use_fallback=True,
                    tenant_id=tenant_id,
                    tenant_cfg=tenant_cfg,
                ):
                    yield chunk
                return
            raise

        # ── 스트리밍 청크 수신 (CB 범위 밖 — 의도된 설계) ────────────────
        async for chunk in stream_obj:
            if not first_chunk_sent:
                elapsed_ms = (time.monotonic() - start) * 1000
                if elapsed_ms > FILLER_THRESHOLD_MS and not filler_triggered:
                    filler_triggered = True
                    import random

                    filler = random.choice(FILLER_PHRASES)  # noqa: S311
                    yield LLMChunk(text=filler, is_final=False, is_filler=True)
                first_chunk_sent = True

            # Groq/OpenAI 호환 응답: usage 는 마지막 청크에만 들어올 수 있음
            usage = getattr(chunk, "usage", None)
            if usage is not None:
                tokens_used = getattr(usage, "total_tokens", 0) or 0

            delta = chunk.choices[0].delta.content or ""  # type: ignore[attr-defined]
            if delta:
                full_text.append(delta)
                yield LLMChunk(text=delta, is_final=False)

        # usage 미수신 시 보수적 추정 (1 토큰 ≈ 영문 4자 / 한글 1.5자)
        joined = "".join(full_text)
        if tokens_used <= 0:
            prompt_chars = sum(len(m.get("content", "")) for m in messages)
            tokens_used = max(1, int((prompt_chars + len(joined)) / 2.5))

        # ── 사용량 commit (tenant_id 있을 때만) ──────────────────────────
        if tenant_id:
            cost_cents = _estimate_cost_cents(model, tokens_used)
            await commit_usage(
                tenant_id,
                tokens=tokens_used,
                cost_cents=cost_cents,
            )
            # Track 3: Prometheus 토큰/비용 누적 — model 레이블 포함
            record_llm_usage(
                tenant_id,
                model=model,
                tokens=tokens_used,
                cost_cents=cost_cents,
            )

        yield LLMChunk(text=joined, is_final=True)

    async def complete(
        self,
        user_text: str,
        history: list[dict] | None = None,
        system_prompt: str | None = None,
        *,
        tenant_id: str | None = None,
        tenant_cfg: dict | None = None,
    ) -> LLMResult:
        """비스트리밍 완성 (배치 처리용)"""
        from app.core.config import settings

        start = time.monotonic()
        full_text = []
        filler_triggered = False

        async for chunk in self.stream(
            user_text,
            history,
            system_prompt,
            tenant_id=tenant_id,
            tenant_cfg=tenant_cfg,
        ):
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
