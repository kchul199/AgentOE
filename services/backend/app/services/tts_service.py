"""
TTS Service — Google Cloud Text-to-Speech Neural2 (ko-KR)
Circuit Breaker 적용, PCM 22kHz 스트리밍 출력
"""
import logging
import time
from dataclasses import dataclass
from typing import Any

from app.core.timeouts import SVC_TTS, with_timeout
from app.domain.circuit_breaker import get_circuit_breaker, make_service_config

logger = logging.getLogger(__name__)


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

    CB 파라미터는 settings.CB_TTS_* 에서 읽으며, make_service_config()가 적용합니다.
    TTS는 recovery_timeout이 STT/LLM(30s)보다 짧습니다(20s).
    Google TTS는 Groq 대비 SLA가 높아 더 빠른 복구를 기대할 수 있습니다.
    """

    def __init__(self) -> None:
        self._cb = get_circuit_breaker("google-tts", make_service_config("google-tts"))
        self._client: Any = None

    def _get_client(self) -> Any:
        """Google TTS 클라이언트 지연 초기화.
        ImportError는 RuntimeError로 감싸 excluded_exceptions에 잡힙니다.
        """
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
        """텍스트를 음성으로 합성. PCM Linear16 반환.

        async with self._cb: 블록 안에서 실제 Google API를 호출합니다.
        RuntimeError(패키지 미설치)는 excluded_exceptions이므로 CB failure_count에
        포함되지 않습니다. 그 외 모든 예외는 failure_count를 증가시킵니다.
        """
        from app.core.config import settings
        from google.cloud import texttospeech

        lang = language_code or settings.GOOGLE_TTS_LANGUAGE
        voice = voice_name or settings.GOOGLE_TTS_VOICE
        start = time.monotonic()

        # 요청 객체 구성은 API 호출이 아니므로 CB 범위 밖
        synthesis_input = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=lang,
            name=voice,
        )
        audio_config = texttospeech.AudioConfig(
            audio_encoding=texttospeech.AudioEncoding.LINEAR16,
            sample_rate_hertz=22050,
        )

        async with self._cb:
            client = self._get_client()
            # TTS_TIMEOUT_SECONDS (기본 3s) 절대 상한. 초과 시 CB failure로 간주.
            response = await with_timeout(
                client.synthesize_speech(
                    input=synthesis_input,
                    voice=voice_params,
                    audio_config=audio_config,
                ),
                service=SVC_TTS,
            )

        # async with 블록 정상 탈출 시에만 도달 (예외 발생 시 호출자로 전파)
        elapsed_ms = (time.monotonic() - start) * 1000
        result = TTSResult(
            audio_bytes=response.audio_content,
            encoding="pcm_22khz",
            sample_rate_hz=22050,
            duration_ms=elapsed_ms,
            char_count=len(text),
        )
        logger.info("TTS complete: %.0fms, %d chars", result.duration_ms, result.char_count)
        return result

    def get_status(self) -> dict:
        return self._cb.get_status()
