"""
Unit tests for VBGW WebSocket Handler (vbgw.py)

테스트 범위:
- JWT 검증 (_decode_token)
- VBGWSession 상태 전이 및 제어 메시지 처리
- 오디오 버퍼 누적 로직
- 파이프라인 실행 (mock)
- 파이프라인 오류 처리 (CircuitBreakerOpenError, generic)
- 세션 종료 처리
- Idle timeout 감지
"""
import asyncio
import base64
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.api.v1.routers.vbgw import (
    MIN_AUDIO_BYTES,
    VBGWSession,
    _decode_token,
    _evt,
)
from app.domain.circuit_breaker import CircuitBreakerOpenError
from app.domain.session_fsm import SessionState
from app.services.ai_pipeline import PipelineResult


# ── 픽스처 ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def mock_ws():
    ws = AsyncMock()
    ws.send_text = AsyncMock()
    ws.close = AsyncMock()
    return ws


@pytest.fixture
def session(mock_ws):
    """테스트용 VBGWSession (AIPipeline은 mock 처리)."""
    s = VBGWSession(
        ws=mock_ws,
        session_id="test-session-001",
        tenant_id="tenant-abc",
        client_id="client-xyz",
    )
    return s


@pytest.fixture
def sample_pipeline_result():
    return PipelineResult(
        stt_text="안녕하세요",
        llm_text="네, 무엇을 도와드릴까요?",
        tts_audio=b"\x00\x01\x02\x03",
        policy_level="G1",
        policy_allowed=True,
        latency={"stt_ms": 200.0, "llm_ms": 300.0, "tts_ms": 150.0, "total_ms": 650.0},
        filler_triggered=False,
    )


# ── JWT 검증 테스트 ────────────────────────────────────────────────────────────


def test_decode_token_invalid_returns_none():
    result = _decode_token("not.a.valid.token")
    assert result is None


def test_decode_token_empty_returns_none():
    result = _decode_token("")
    assert result is None


def test_decode_valid_token():
    """유효한 JWT 토큰 검증."""
    from app.core.auth import create_access_token
    token = create_access_token(
        tenant_id="tenant-001",
        client_id="client-001",
        roles=["agent"],
    )
    payload = _decode_token(token)
    assert payload is not None
    assert payload["tenant_id"] == "tenant-001"
    assert payload["sub"] == "client-001"


# ── 이벤트 헬퍼 테스트 ──────────────────────────────────────────────────────────


def test_evt_creates_valid_json():
    result = _evt("test_event", key="value", num=42)
    parsed = json.loads(result)
    assert parsed["event"] == "test_event"
    assert parsed["key"] == "value"
    assert parsed["num"] == 42


# ── VBGWSession 상태 전이 테스트 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initial_state_is_idle(session):
    assert session.fsm.state == SessionState.IDLE


@pytest.mark.asyncio
async def test_start_listening_transitions_to_listening(session):
    await session.handle_control({"action": "start_listening"})
    assert session.fsm.state == SessionState.LISTENING
    # state_change 이벤트가 전송됐는지 확인
    call_args = session.ws.send_text.call_args_list
    events = [json.loads(c[0][0]) for c in call_args]
    state_events = [e for e in events if e.get("event") == "state_change"]
    assert any(e["state"] == "LISTENING" for e in state_events)


@pytest.mark.asyncio
async def test_hangup_transitions_to_ended(session):
    await session.handle_control({"action": "hangup"})
    assert session.fsm.state == SessionState.ENDED


@pytest.mark.asyncio
async def test_ping_sends_pong(session):
    await session.handle_control({"action": "ping"})
    sent = [json.loads(c[0][0]) for c in session.ws.send_text.call_args_list]
    assert any(e["event"] == "pong" for e in sent)


@pytest.mark.asyncio
async def test_unknown_action_sends_error(session):
    await session.handle_control({"action": "unknown_action_xyz"})
    sent = [json.loads(c[0][0]) for c in session.ws.send_text.call_args_list]
    assert any(e["event"] == "error" and e["code"] == "UNKNOWN_ACTION" for e in sent)


# ── 오디오 버퍼 테스트 ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_audio_ignored_before_start_listening(session):
    """start_listening 이전에 오디오 수신 시 버퍼에 쌓이지 않아야 함."""
    audio = b"\x00" * MIN_AUDIO_BYTES
    with patch.object(session, "_run_pipeline", new_callable=AsyncMock) as mock_run:
        await session.handle_audio(audio)
        mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_audio_accumulates_until_threshold(session):
    """MIN_AUDIO_BYTES 미만 오디오는 파이프라인 실행 안 함."""
    await session.handle_control({"action": "start_listening"})

    small_chunk = b"\x00" * (MIN_AUDIO_BYTES // 2)
    with patch.object(session, "_run_pipeline", new_callable=AsyncMock) as mock_run:
        await session.handle_audio(small_chunk)
        mock_run.assert_not_called()


@pytest.mark.asyncio
async def test_audio_triggers_pipeline_at_threshold(session):
    """MIN_AUDIO_BYTES 이상이면 파이프라인 실행."""
    await session.handle_control({"action": "start_listening"})

    large_chunk = b"\x00" * MIN_AUDIO_BYTES
    with patch.object(session, "_run_pipeline", new_callable=AsyncMock) as mock_run:
        await session.handle_audio(large_chunk)
        mock_run.assert_called_once()


# ── 파이프라인 실행 테스트 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_pipeline_sends_all_events(session, sample_pipeline_result):
    """파이프라인 성공 시 stt_result, llm_chunk, tts_ready, pipeline_done 이벤트 전송."""
    with patch.object(session.pipeline, "process", new=AsyncMock(return_value=sample_pipeline_result)):
        await session._run_pipeline(b"\x00" * 100)

    sent = [json.loads(c[0][0]) for c in session.ws.send_text.call_args_list]
    event_names = [e["event"] for e in sent]

    assert "stt_result" in event_names
    assert "llm_chunk" in event_names
    assert "tts_ready" in event_names
    assert "pipeline_done" in event_names


@pytest.mark.asyncio
async def test_run_pipeline_sends_audio_base64(session, sample_pipeline_result):
    """TTS 오디오가 base64 인코딩되어 전송돼야 함."""
    with patch.object(session.pipeline, "process", new=AsyncMock(return_value=sample_pipeline_result)):
        await session._run_pipeline(b"\x00" * 100)

    sent = [json.loads(c[0][0]) for c in session.ws.send_text.call_args_list]
    tts_evt = next(e for e in sent if e["event"] == "tts_ready")
    decoded = base64.b64decode(tts_evt["audio_b64"])
    assert decoded == sample_pipeline_result.tts_audio


@pytest.mark.asyncio
async def test_run_pipeline_updates_history(session, sample_pipeline_result):
    """파이프라인 완료 후 대화 히스토리가 업데이트돼야 함."""
    with patch.object(session.pipeline, "process", new=AsyncMock(return_value=sample_pipeline_result)):
        await session._run_pipeline(b"\x00" * 100)

    assert len(session.history) == 2
    assert session.history[0]["role"] == "user"
    assert session.history[0]["content"] == sample_pipeline_result.stt_text
    assert session.history[1]["role"] == "assistant"
    assert session.history[1]["content"] == sample_pipeline_result.llm_text


@pytest.mark.asyncio
async def test_run_pipeline_policy_blocked(session):
    """PolicyGate 차단 시 error 이벤트 전송 후 LISTENING으로 복귀."""
    blocked_result = PipelineResult(
        stt_text="나쁜 말",
        llm_text="[POLICY_BLOCKED:G3]",
        tts_audio=None,
        policy_level="G3",
        policy_allowed=False,
        latency={},
    )
    with patch.object(session.pipeline, "process", new=AsyncMock(return_value=blocked_result)):
        await session._run_pipeline(b"\x00" * 100)

    sent = [json.loads(c[0][0]) for c in session.ws.send_text.call_args_list]
    errors = [e for e in sent if e["event"] == "error" and e["code"] == "POLICY_BLOCKED"]
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_run_pipeline_circuit_breaker_open(session):
    """CircuitBreakerOpenError 발생 시 SERVICE_UNAVAILABLE 에러 이벤트 전송."""
    with patch.object(
        session.pipeline, "process",
        side_effect=CircuitBreakerOpenError("groq-stt"),
    ):
        await session._run_pipeline(b"\x00" * 100)

    sent = [json.loads(c[0][0]) for c in session.ws.send_text.call_args_list]
    errors = [e for e in sent if e["event"] == "error" and e["code"] == "SERVICE_UNAVAILABLE"]
    assert len(errors) == 1


@pytest.mark.asyncio
async def test_run_pipeline_generic_error(session):
    """일반 예외 발생 시 PIPELINE_ERROR 이벤트 전송."""
    with patch.object(
        session.pipeline, "process",
        side_effect=RuntimeError("unexpected error"),
    ):
        await session._run_pipeline(b"\x00" * 100)

    sent = [json.loads(c[0][0]) for c in session.ws.send_text.call_args_list]
    errors = [e for e in sent if e["event"] == "error" and e["code"] == "PIPELINE_ERROR"]
    assert len(errors) == 1


# ── 히스토리 최대 길이 테스트 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_history_capped_at_20_entries(session, sample_pipeline_result):
    """대화 히스토리가 20개를 초과하지 않아야 함."""
    with patch.object(session.pipeline, "process", new=AsyncMock(return_value=sample_pipeline_result)):
        with patch.object(session, "_persist_state", new=AsyncMock()):
            for _ in range(15):
                await session._run_pipeline(b"\x00" * 100)

    assert len(session.history) <= 20


# ── 세션 종료 테스트 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_end_session_transitions_to_ended(session):
    await session._end_session(reason="test")
    assert session.fsm.state == SessionState.ENDED


@pytest.mark.asyncio
async def test_end_session_idempotent(session):
    """중복 종료 호출이 예외를 발생시키지 않아야 함."""
    await session._end_session()
    await session._end_session()  # 두 번 호출해도 안전
    assert session.fsm.state == SessionState.ENDED


# ── Idle Timeout 테스트 ───────────────────────────────────────────────────────


def test_is_idle_timeout_false_for_fresh_session(session):
    assert session.is_idle_timeout is False


def test_is_idle_timeout_true_after_long_inactivity(session):
    session._last_activity = time.monotonic() - 200  # 200초 전
    assert session.is_idle_timeout is True


# ── stop_listening 잔여 버퍼 처리 테스트 ──────────────────────────────────────


@pytest.mark.asyncio
async def test_stop_listening_flushes_buffer(session):
    """stop_listening 시 잔여 버퍼가 파이프라인으로 전달돼야 함."""
    await session.handle_control({"action": "start_listening"})
    # MIN_AUDIO_BYTES 미만 오디오를 버퍼에 쌓기
    small = b"\x00" * (MIN_AUDIO_BYTES // 2)
    session._audio_buffer.extend(small)

    with patch.object(session, "_run_pipeline", new_callable=AsyncMock) as mock_run:
        await session.handle_control({"action": "stop_listening"})
        mock_run.assert_called_once_with(small)
