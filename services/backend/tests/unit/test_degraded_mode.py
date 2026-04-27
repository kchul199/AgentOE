"""
Unit tests for AI Pipeline Degraded Mode

테스트 범위:
  - STT 장애 시 degraded=True, stt_text="" 반환
  - LLM 장애 시 degraded=True, fallback 메시지 반환
  - TTS 장애 시 degraded_stage="tts", audio_bytes=None, llm_text 정상
  - 각 Degraded 케이스에서 record_pipeline_call degraded=True 호출 확인
  - 정상 파이프라인에서 degraded=False 확인
  - DEGRADED_MESSAGES 내용이 비어있지 않음
  - fsm.record_event(VENDOR_DEGRADED) 호출 확인
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

# 외부 의존성 mock
for mod in [
    "motor", "motor.motor_asyncio", "pymongo", "pymongo.errors",
    "redis", "redis.asyncio",
    "groq", "google.cloud", "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1", "grpc",
]:
    if mod not in sys.modules:
        sys.modules[mod] = mock.MagicMock()

from app.domain.circuit_breaker import CircuitBreakerOpenError
from app.domain.policy_gate import PolicyLevel
from app.domain.session_fsm import SessionEventType, SessionFSM, SessionState
from app.services.ai_pipeline import AIPipeline, DEGRADED_MESSAGES, PipelineResult
from app.services.stt_service import STTResult
from app.services.llm_service import LLMResult
from app.services.tts_service import TTSResult


# ── 픽스처 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fsm():
    return SessionFSM(SessionState.IDLE)


@pytest.fixture
def pipeline():
    return AIPipeline()


@pytest.fixture
def normal_stt():
    return STTResult(
        text="배송 조회해 주세요",
        confidence=0.95,
        is_final=True,
        duration_ms=200.0,
        model="whisper",
    )


@pytest.fixture
def normal_llm():
    return LLMResult(
        full_text="배송 정보를 확인해 드리겠습니다.",
        duration_ms=300.0,
        model="llama",
        filler_triggered=False,
    )


@pytest.fixture
def normal_tts():
    return TTSResult(audio_bytes=b"\x00\x01\x02\x03", duration_ms=100.0)


# ── 정상 파이프라인 ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_normal_pipeline_not_degraded(pipeline, fsm, normal_stt, normal_llm, normal_tts):
    """정상 파이프라인 → degraded=False."""
    pipeline._stt.transcribe = AsyncMock(return_value=normal_stt)
    pipeline._llm.complete = AsyncMock(return_value=normal_llm)
    pipeline._tts.synthesize = AsyncMock(return_value=normal_tts)

    with patch("app.services.ai_pipeline.record_pipeline_call") as mock_record:
        result = await pipeline.process(
            audio_bytes=b"\x00" * 100,
            session_id="s1",
            tenant_id="t1",
            fsm=fsm,
        )

    assert result.degraded is False
    assert result.degraded_stage is None
    assert result.stt_text == "배송 조회해 주세요"
    assert result.llm_text == "배송 정보를 확인해 드리겠습니다."
    assert result.tts_audio == b"\x00\x01\x02\x03"
    assert result.policy_allowed is True

    mock_record.assert_called_once()
    _, kwargs = mock_record.call_args
    assert kwargs["success"] is True
    assert kwargs["degraded"] is False


@pytest.mark.asyncio
async def test_normal_pipeline_records_latency(pipeline, fsm, normal_stt, normal_llm, normal_tts):
    """정상 파이프라인 → 레이턴시 레코딩 확인."""
    pipeline._stt.transcribe = AsyncMock(return_value=normal_stt)
    pipeline._llm.complete = AsyncMock(return_value=normal_llm)
    pipeline._tts.synthesize = AsyncMock(return_value=normal_tts)

    with patch("app.services.ai_pipeline.record_pipeline_call") as mock_record:
        await pipeline.process(b"\x00", "s1", "t1", fsm)

    _, kwargs = mock_record.call_args
    assert kwargs["total_ms"] > 0
    assert kwargs["stt_ms"] > 0
    assert kwargs["llm_ms"] > 0
    assert kwargs["tts_ms"] > 0


# ── STT Degraded Mode ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_stt_circuit_breaker_open_returns_degraded(pipeline, fsm):
    """STT CircuitBreakerOpenError → degraded 응답."""
    pipeline._stt.transcribe = AsyncMock(
        side_effect=CircuitBreakerOpenError("groq-stt")
    )

    with patch("app.services.ai_pipeline.record_pipeline_call") as mock_record:
        result = await pipeline.process(b"\x00", "s1", "t1", fsm)

    assert result.degraded is True
    assert result.degraded_stage == "stt"
    assert result.stt_text == ""
    assert DEGRADED_MESSAGES["stt_unavailable"] in result.llm_text
    assert result.tts_audio is None

    _, kwargs = mock_record.call_args
    assert kwargs["success"] is False
    assert kwargs["degraded"] is True


@pytest.mark.asyncio
async def test_stt_generic_exception_returns_degraded(pipeline, fsm):
    """STT 일반 예외 → degraded 응답."""
    pipeline._stt.transcribe = AsyncMock(side_effect=RuntimeError("timeout"))

    result = await pipeline.process(b"\x00", "s1", "t1", fsm)

    assert result.degraded is True
    assert result.degraded_stage == "stt"


@pytest.mark.asyncio
async def test_stt_degraded_records_vendor_degraded_event(pipeline, fsm):
    """STT 장애 시 FSM에 VENDOR_DEGRADED 이벤트 기록."""
    pipeline._stt.transcribe = AsyncMock(
        side_effect=CircuitBreakerOpenError("groq-stt")
    )
    await pipeline.process(b"\x00", "s1", "t1", fsm)

    event_types = [e.event_type for e in fsm.events]
    assert SessionEventType.VENDOR_DEGRADED in event_types


@pytest.mark.asyncio
async def test_stt_degraded_skips_llm_and_tts(pipeline, fsm):
    """STT 장애 시 LLM/TTS 호출하지 않음."""
    pipeline._stt.transcribe = AsyncMock(side_effect=RuntimeError("stt down"))
    pipeline._llm.complete = AsyncMock()
    pipeline._tts.synthesize = AsyncMock()

    await pipeline.process(b"\x00", "s1", "t1", fsm)

    pipeline._llm.complete.assert_not_called()
    pipeline._tts.synthesize.assert_not_called()


# ── LLM Degraded Mode ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_circuit_breaker_open_returns_degraded(
    pipeline, fsm, normal_stt, normal_tts
):
    """LLM CircuitBreakerOpenError → degraded 응답, TTS 건너뜀."""
    pipeline._stt.transcribe = AsyncMock(return_value=normal_stt)
    pipeline._llm.complete = AsyncMock(
        side_effect=CircuitBreakerOpenError("groq-llm-primary")
    )
    pipeline._tts.synthesize = AsyncMock(return_value=normal_tts)

    result = await pipeline.process(b"\x00", "s1", "t1", fsm)

    assert result.degraded is True
    assert result.degraded_stage == "llm"
    assert DEGRADED_MESSAGES["llm_unavailable"] in result.llm_text
    # LLM 장애 시 TTS 호출하지 않음 (degraded=True 분기)
    pipeline._tts.synthesize.assert_not_called()


@pytest.mark.asyncio
async def test_llm_degraded_records_vendor_event(pipeline, fsm, normal_stt):
    pipeline._stt.transcribe = AsyncMock(return_value=normal_stt)
    pipeline._llm.complete = AsyncMock(side_effect=RuntimeError("llm down"))

    await pipeline.process(b"\x00", "s1", "t1", fsm)

    event_types = [e.event_type for e in fsm.events]
    assert SessionEventType.VENDOR_DEGRADED in event_types


# ── TTS Degraded Mode ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_tts_failure_returns_text_only(pipeline, fsm, normal_stt, normal_llm):
    """TTS 장애 → llm_text 정상, audio_bytes=None (text-only 응답)."""
    pipeline._stt.transcribe = AsyncMock(return_value=normal_stt)
    pipeline._llm.complete = AsyncMock(return_value=normal_llm)
    pipeline._tts.synthesize = AsyncMock(
        side_effect=CircuitBreakerOpenError("google-tts")
    )

    result = await pipeline.process(b"\x00", "s1", "t1", fsm)

    # TTS만 장애 → llm_text는 정상, degraded=False (TTS만 없음)
    assert result.llm_text == normal_llm.full_text
    assert result.tts_audio is None
    assert result.degraded_stage == "tts"


@pytest.mark.asyncio
async def test_tts_failure_records_vendor_event(pipeline, fsm, normal_stt, normal_llm):
    pipeline._stt.transcribe = AsyncMock(return_value=normal_stt)
    pipeline._llm.complete = AsyncMock(return_value=normal_llm)
    pipeline._tts.synthesize = AsyncMock(side_effect=RuntimeError("tts down"))

    await pipeline.process(b"\x00", "s1", "t1", fsm)

    event_types = [e.event_type for e in fsm.events]
    assert SessionEventType.VENDOR_DEGRADED in event_types


# ── DEGRADED_MESSAGES 내용 검증 ───────────────────────────────────────────────


def test_degraded_messages_not_empty():
    assert DEGRADED_MESSAGES["stt_unavailable"]
    assert DEGRADED_MESSAGES["llm_unavailable"]
    assert DEGRADED_MESSAGES["all_unavailable"]


def test_degraded_messages_in_korean():
    """Fallback 메시지는 한국어여야 함."""
    for key in ["stt_unavailable", "llm_unavailable", "all_unavailable"]:
        msg = DEGRADED_MESSAGES[key]
        # 한국어 텍스트에는 한글 유니코드 범위 포함
        has_korean = any("\uAC00" <= c <= "\uD7A3" for c in msg)
        assert has_korean, f"'{key}' 메시지에 한글 없음"


# ── PipelineResult 데이터클래스 검증 ─────────────────────────────────────────


def test_pipeline_result_default_not_degraded():
    result = PipelineResult(
        stt_text="test",
        llm_text="response",
        tts_audio=None,
        policy_level="G1",
        policy_allowed=True,
        latency={},
    )
    assert result.degraded is False
    assert result.degraded_stage is None
    assert result.filler_triggered is False
