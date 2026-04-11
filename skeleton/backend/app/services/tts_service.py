"""
TTS Service — Google Cloud Text-to-Speech Neural2 (ko-KR)
Circuit Breaker 적용, PCM 22kHz 스트리밍 출력
"""
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.domain.circuit_breaker import CircuitBreakerConfig, get_circuit_breaker

logger = logging.getLogger(__name__)

TTS_CB_CONFIG = CircuitBreakerConfig(
    failure_threshold=5,
    recovery_timeout=20.0,
    half_open_max_calls=3,
    success_threshold=2,
)


@dataclass
class TTSResult:
    audio_bytes: bytes
    encoding: str
    sample_rate_hz: int
    duration_ms: float
    char_count: int


class TTSService:
    """
    Google Neural2 TTS 서비스.
    Circuit Breaker 내장.
    """

    def __init__(self) -> None:
        self._cb = get_circuit_breaker("google-tts", TTS_CB_CONFIG)
        self._client: Any = None

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                from google.cloud import texttospeech
                self._client = texttospeech.TextToSpeechAsyncClient()
            except ImportError:
                raise RuntimeError("google-cloud-texttospeech not installed")
        return self._client

    async def synthesize(
        self,
        text: str,
        language_code: str | None = None,
        voice_name: str | None = None,
    ) -> TTSResult:
        """텍스트를 음성으로 합성. PCM Linear16 반환."""
        from app.core.config import settings
        lang = language_code or settings.GOOGLE_TTS_LANGUAGE
        voice = voice_name or settings.GOOGLE_TTS_VOICE
        start = time.monotonic()

        async def _call() -> TTSResult:
            from google.cloud import texttospeech
            client = self._get_client()
            synthesis_input = texttospeech.SynthesisInput(text=text)
            voice_params = texttospeech.VoiceSelectionParams(
                language_code=lang,
                name=voice,
            )
            audio_config = texttospeech.AudioConfig(
                audio_encoding=texttospeech.AudioEncoding.LINEAR16,
                sample_rate_hertz=22050,
            )
            response = await client.synthesize_speech(
                input=synthesis_input,
                voice=voice_params,
                audio_config=audio_config,
            )
            elapsed_ms = (time.monotonic() - start) * 1000
            return TTSResult(
                audio_bytes=response.audio_content,
                encoding="pcm_22khz",
                sample_rate_hz=22050,
                duration_ms=elapsed_ms,
                char_count=len(text),
            )

        result = await self._cb.call(_call)
        logger.info("TTS complete: %.0fms, %d chars", result.duration_ms, result.char_count)
        return result

    def get_status(self) -> dict:
        return self._cb.get_status()
