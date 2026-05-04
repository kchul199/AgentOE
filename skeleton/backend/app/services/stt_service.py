"""
STT Service — Groq Whisper Large v3 Turbo
Circuit Breaker 패턴 적용, 장애 시 Fallback 처리
"""
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.core.timeouts import SVC_STT, with_timeout
from app.domain.circuit_breaker import get_circuit_breaker, make_service_config

logger = logging.getLogger(__name__)


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

    CB 파라미터는 settings.CB_STT_* 에서 읽으며, make_service_config()가 적용합니다.
    """

    def __init__(self) -> None:
        self._cb = get_circuit_breaker("groq-stt", make_service_config("groq-stt"))
        self._client: Any = None

    def _get_client(self) -> Any:
        """Groq 클라이언트 지연 초기화.
        ImportError는 RuntimeError로 감싸 excluded_exceptions에 잡힙니다.
        """
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
        """오디오 바이트를 텍스트로 전사.

        async with self._cb: 블록 안에서 실제 Groq API를 호출합니다.
        RuntimeError(패키지 미설치)는 excluded_exceptions이므로 CB failure_count에
        포함되지 않습니다. 그 외 모든 예외는 failure_count를 증가시킵니다.
        """
        from app.core.config import settings
        import io

        start = time.monotonic()

        async with self._cb:
            client = self._get_client()
            # STT_TIMEOUT_SECONDS 절대 상한 적용. 초과 시 ExternalTimeoutError → CB failure.
            response = await with_timeout(
                client.audio.transcriptions.create(
                    file=("audio.wav", io.BytesIO(audio_bytes), "audio/wav"),
                    model=settings.GROQ_STT_MODEL,
                    language=language,
                    response_format="verbose_json",
                ),
                service=SVC_STT,
            )

        # async with 블록 정상 탈출 시에만 도달 (예외 발생 시 호출자로 전파)
        elapsed_ms = (time.monotonic() - start) * 1000
        result = STTResult(
            text=response.text,
            confidence=getattr(response, "avg_logprob", 0.9),
            is_final=True,
            duration_ms=elapsed_ms,
            model=settings.GROQ_STT_MODEL,
            language=language,
        )
        logger.info(
            "STT complete: %.0fms, text='%s...'",
            result.duration_ms, result.text[:30],
        )
        return result

    def get_status(self) -> dict:
        return self._cb.get_status()
