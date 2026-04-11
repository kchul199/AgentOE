"""
STT Service — Groq Whisper Large v3 Turbo
Circuit Breaker 패턴 적용, 장애 시 Fallback 처리
"""
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.domain.circuit_breaker import CircuitBreakerConfig, get_circuit_breaker

logger = logging.getLogger(__name__)

STT_CB_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    recovery_timeout=30.0,
    half_open_max_calls=3,
    success_threshold=2,
)


@dataclass
class STTResult:
    text: str
    confidence: float
    is_final: bool
    duration_ms: float
    model: str
    language: str = "ko"


class STTService:
    """
    Groq Whisper Large v3 Turbo STT 서비스.
    Circuit Breaker 내장, 장애 시 에러 반환 (Fallback은 오케스트레이터에서 처리).
    """

    def __init__(self) -> None:
        self._cb = get_circuit_breaker("groq-stt", STT_CB_CONFIG)
        self._client: Any = None

    def _get_client(self) -> Any:
        """Groq 클라이언트 지연 초기화"""
        if self._client is None:
            try:
                from groq import AsyncGroq
                from app.core.config import settings
                self._client = AsyncGroq(api_key=settings.GROQ_API_KEY)
            except ImportError:
                logger.warning("groq package not installed — STT unavailable")
                raise RuntimeError("groq package not installed")
        return self._client

    async def transcribe(self, audio_bytes: bytes, language: str = "ko") -> STTResult:
        """오디오 바이트를 텍스트로 전사"""
        start = time.monotonic()

        async def _call() -> STTResult:
            client = self._get_client()
            from app.core.config import settings
            import io
            response = await client.audio.transcriptions.create(
                file=("audio.wav", io.BytesIO(audio_bytes), "audio/wav"),
                model=settings.GROQ_STT_MODEL,
                language=language,
                response_format="verbose_json",
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            return STTResult(
                text=response.text,
                confidence=getattr(response, "avg_logprob", 0.9),
                is_final=True,
                duration_ms=elapsed_ms,
                model=settings.GROQ_STT_MODEL,
                language=language,
            )

        result = await self._cb.call(_call)
        logger.info("STT complete: %.0fms, text='%s...'",
                    result.duration_ms, result.text[:30])
        return result

    def get_status(self) -> dict:
        return self._cb.get_status()
