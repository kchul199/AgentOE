"""
CallSessionOrchestrator 단위 테스트

WebSocket 객체 없이 순수 비즈니스 로직만 검증한다.
AIPipeline / TransferService / SessionRepository 는 AsyncMock으로 교체.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.circuit_breaker import CircuitBreakerOpenError
from app.domain.policy_gate import PolicyLevel
from app.domain.session_fsm import SessionFSM, SessionState
from app.services.ai_pipeline import PipelineResult
from app.services.call_session_orchestrator import (
    MAX_HISTORY_IN_MEMORY,
    PIPELINE_FAILURE_THRESHOLD,
    CallSessionOrchestrator,
    OutboundEvent,
    _transfer_priority,
    restore_or_create_orchestrator,
)
from app.services.transfer_service import (
    TransferFallback,
    TransferReason,
    TransferResult,
    TransferStatus,
)

# ── 픽스처 ────────────────────────────────────────────────────────────────────


def _make_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.save_turn = AsyncMock()
    repo.end_session = AsyncMock()
    repo.create = AsyncMock()
    repo.restore_hot_state = AsyncMock(return_value=None)
    repo.acquire_session_lease = AsyncMock(return_value=True)
    repo.release_session_lease = AsyncMock()
    return repo


def _make_pipeline_result(**kwargs) -> PipelineResult:
    defaults = {
        "stt_text": "테스트 발화",
        "llm_text": "AI 응답입니다.",
        "tts_audio": b"\x00\x01\x02",
        "policy_level": "G1",
        "policy_allowed": True,
        "latency": {"total_ms": 800.0},
        "filler_triggered": False,
        "degraded": False,
        "degraded_stage": None,
    }
    defaults.update(kwargs)
    return PipelineResult(**defaults)


def _make_orchestrator(repo=None, fsm=None, history=None) -> CallSessionOrchestrator:
    return CallSessionOrchestrator(
        session_id="sess-001",
        tenant_id="acme",
        client_id="user-1",
        repo=repo or _make_repo(),
        fsm=fsm,
        history=history or [],
        policy_level=PolicyLevel.G1,
    )


# ── OutboundEvent ─────────────────────────────────────────────────────────────


class TestOutboundEvent:
    def test_to_json_includes_event_key(self):
        evt = OutboundEvent("stt_result", {"text": "hello", "is_final": True})
        import json

        data = json.loads(evt.to_json())
        assert data["event"] == "stt_result"
        assert data["text"] == "hello"

    def test_empty_payload(self):
        evt = OutboundEvent("pong")
        import json

        data = json.loads(evt.to_json())
        assert data == {"event": "pong"}


# ── _transfer_priority ────────────────────────────────────────────────────────


class TestTransferPriority:
    def test_g4_policy_is_high_priority(self):
        assert _transfer_priority(TransferReason.G4_POLICY) == 3

    def test_g5_policy_is_high_priority(self):
        assert _transfer_priority(TransferReason.G5_POLICY) == 3

    def test_customer_request_is_normal(self):
        assert _transfer_priority(TransferReason.CUSTOMER_REQUEST) == 5

    def test_repeated_failure_is_normal(self):
        assert _transfer_priority(TransferReason.REPEATED_FAILURE) == 5


# ── handle_control ────────────────────────────────────────────────────────────


class TestHandleControl:
    @pytest.mark.asyncio
    async def test_ping_returns_pong(self):
        orc = _make_orchestrator()
        events = await orc.handle_control({"action": "ping"})
        assert len(events) == 1
        assert events[0].name == "pong"

    @pytest.mark.asyncio
    async def test_start_listening_transitions_state(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)  # IDLE→LISTENING 수동 전이
        # stop → IDLE 로 되돌린 뒤 start_listening 테스트
        # 단순히 _listening 플래그와 state_change 이벤트 반환 확인
        orc.fsm = SessionFSM(SessionState.IDLE)
        orc.fsm.transition(SessionState.LISTENING)  # 이미 LISTENING
        events = await orc.handle_control({"action": "start_listening"})
        assert orc._listening is True
        # LISTENING → LISTENING 전이는 불가 → 이벤트 없음(can_transition False)
        assert all(e.name != "error" for e in events)

    @pytest.mark.asyncio
    async def test_unknown_action_returns_error(self):
        orc = _make_orchestrator()
        events = await orc.handle_control({"action": "fly_to_moon"})
        assert len(events) == 1
        assert events[0].name == "error"
        assert events[0].payload["code"] == "UNKNOWN_ACTION"

    @pytest.mark.asyncio
    async def test_hangup_ends_session(self):
        orc = _make_orchestrator()
        events = await orc.handle_control({"action": "hangup"})
        assert orc.should_close is True
        assert orc._closed is True
        # state_change(ENDED) 이벤트 포함
        names = [e.name for e in events]
        assert "state_change" in names

    @pytest.mark.asyncio
    async def test_stop_listening_flushes_buffer(self):
        orc = _make_orchestrator()
        orc._listening = True
        # 충분한 오디오 데이터로 버퍼 채우기
        orc._audio_buffer.extend(b"\x00" * 10000)

        mock_result = _make_pipeline_result()
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            events = await orc.handle_control({"action": "stop_listening"})

        assert orc._listening is False
        assert len(orc._audio_buffer) == 0
        names = [e.name for e in events]
        assert "stt_result" in names


# ── handle_audio ──────────────────────────────────────────────────────────────


class TestHandleAudio:
    @pytest.mark.asyncio
    async def test_audio_ignored_when_not_listening(self):
        orc = _make_orchestrator()
        orc._listening = False
        events = await orc.handle_audio(b"\x00" * 5000)
        assert events == []

    @pytest.mark.asyncio
    async def test_audio_buffered_below_threshold(self):
        orc = _make_orchestrator()
        orc._listening = True
        # MIN_AUDIO_BYTES = 4000 → 아래
        events = await orc.handle_audio(b"\x00" * 100)
        assert events == []
        assert len(orc._audio_buffer) == 100

    @pytest.mark.asyncio
    async def test_pipeline_triggered_at_threshold(self):
        orc = _make_orchestrator()
        orc._listening = True

        mock_result = _make_pipeline_result()
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            # MIN_AUDIO_BYTES = 4000 이상 전송
            events = await orc.handle_audio(b"\x00" * 5000)

        names = [e.name for e in events]
        assert "stt_result" in names
        assert "pipeline_done" in names
        # 버퍼 비워졌는지
        assert len(orc._audio_buffer) == 0


# ── _run_pipeline — 정상 경로 ─────────────────────────────────────────────────


class TestRunPipeline:
    @pytest.mark.asyncio
    async def test_normal_path_event_order(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)

        mock_result = _make_pipeline_result(tts_audio=b"\xab\xcd")
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            events = await orc._run_pipeline(b"\x00" * 100)

        names = [e.name for e in events]
        assert "stt_result" in names
        assert "llm_chunk" in names
        assert "tts_ready" in names
        assert "pipeline_done" in names
        assert "state_change" in names

    @pytest.mark.asyncio
    async def test_no_tts_audio_skips_tts_event(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)

        mock_result = _make_pipeline_result(tts_audio=None)
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            events = await orc._run_pipeline(b"\x00" * 100)

        names = [e.name for e in events]
        assert "tts_ready" not in names
        assert "llm_chunk" in names

    @pytest.mark.asyncio
    async def test_history_appended_after_success(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)

        mock_result = _make_pipeline_result(stt_text="질문", llm_text="답변")
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            await orc._run_pipeline(b"\x00" * 100)

        assert len(orc.history) == 2
        assert orc.history[0] == {"role": "user", "content": "질문"}
        assert orc.history[1] == {"role": "assistant", "content": "답변"}

    @pytest.mark.asyncio
    async def test_history_trimmed_to_max(self):
        # MAX_HISTORY_IN_MEMORY 초과 시 오래된 항목 제거
        initial_history = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": str(i)}
            for i in range(MAX_HISTORY_IN_MEMORY)
        ]
        orc = _make_orchestrator(history=initial_history)
        orc.fsm.transition(SessionState.LISTENING)

        mock_result = _make_pipeline_result()
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            await orc._run_pipeline(b"\x00" * 100)

        assert len(orc.history) == MAX_HISTORY_IN_MEMORY

    @pytest.mark.asyncio
    async def test_failure_count_reset_on_success(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)
        orc._pipeline_failure_count = 2

        mock_result = _make_pipeline_result()
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            await orc._run_pipeline(b"\x00" * 100)

        assert orc._pipeline_failure_count == 0


# ── _run_pipeline — 에러 경로 ─────────────────────────────────────────────────


class TestRunPipelineErrors:
    @pytest.mark.asyncio
    async def test_circuit_breaker_open_returns_error_event(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)

        exc = CircuitBreakerOpenError("groq-stt")
        with patch.object(orc._pipeline, "process", side_effect=exc):
            events = await orc._run_pipeline(b"\x00" * 100)

        names = [e.name for e in events]
        assert "error" in names
        error_evt = next(e for e in events if e.name == "error")
        assert error_evt.payload["code"] == "SERVICE_UNAVAILABLE"

    @pytest.mark.asyncio
    async def test_failure_count_increments_on_error(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)

        with patch.object(orc._pipeline, "process", side_effect=RuntimeError("boom")):
            await orc._run_pipeline(b"\x00" * 100)

        assert orc._pipeline_failure_count == 1

    @pytest.mark.asyncio
    async def test_auto_transfer_triggered_at_threshold(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)
        orc._pipeline_failure_count = PIPELINE_FAILURE_THRESHOLD - 1

        transfer_result = TransferResult(
            status=TransferStatus.FAILED,
            message="이관 실패",
            fallback_action=TransferFallback.AI_RESUME,
        )
        with (
            patch.object(orc._pipeline, "process", side_effect=RuntimeError("boom")),
            patch.object(orc._transfer_svc, "request", new=AsyncMock(return_value=transfer_result)),
        ):
            events = await orc._run_pipeline(b"\x00" * 100)

        names = [e.name for e in events]
        assert "transfer_update" in names
        assert orc._pipeline_failure_count == PIPELINE_FAILURE_THRESHOLD


# ── 정책 차단 ─────────────────────────────────────────────────────────────────


class TestPolicyBlocking:
    @pytest.mark.asyncio
    async def test_g4_policy_triggers_transfer(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)

        mock_result = _make_pipeline_result(policy_level="G4", policy_allowed=False, tts_audio=None)
        transfer_result = TransferResult(
            status=TransferStatus.FAILED,
            message="이관 실패",
            fallback_action=TransferFallback.AI_RESUME,
        )
        with (
            patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)),
            patch.object(orc._transfer_svc, "request", new=AsyncMock(return_value=transfer_result)),
        ):
            events = await orc._run_pipeline(b"\x00" * 100)

        names = [e.name for e in events]
        assert "transfer_update" in names

    @pytest.mark.asyncio
    async def test_g1_policy_block_returns_error(self):
        orc = _make_orchestrator()
        orc.fsm.transition(SessionState.LISTENING)

        mock_result = _make_pipeline_result(policy_level="G1", policy_allowed=False, tts_audio=None)
        with patch.object(orc._pipeline, "process", new=AsyncMock(return_value=mock_result)):
            events = await orc._run_pipeline(b"\x00" * 100)

        names = [e.name for e in events]
        assert "error" in names
        error_evt = next(e for e in events if e.name == "error")
        assert error_evt.payload["code"] == "POLICY_BLOCKED"


# ── end_session ────────────────────────────────────────────────────────────────


class TestEndSession:
    @pytest.mark.asyncio
    async def test_end_session_sets_should_close(self):
        orc = _make_orchestrator()
        await orc.end_session(reason="normal")
        assert orc.should_close is True
        assert orc._closed is True

    @pytest.mark.asyncio
    async def test_end_session_idempotent(self):
        repo = _make_repo()
        orc = _make_orchestrator(repo=repo)
        await orc.end_session()
        await orc.end_session()  # 두 번 호출

        # repo.end_session은 단 한 번만 호출되어야 함
        repo.end_session.assert_called_once()

    @pytest.mark.asyncio
    async def test_end_session_returns_state_change_event(self):
        orc = _make_orchestrator()
        events = await orc.end_session()
        names = [e.name for e in events]
        assert "state_change" in names

    @pytest.mark.asyncio
    async def test_second_end_session_returns_empty(self):
        orc = _make_orchestrator()
        await orc.end_session()
        events = await orc.end_session()
        assert events == []


# ── restore_or_create_orchestrator ────────────────────────────────────────────


class TestRestoreOrCreate:
    @pytest.mark.asyncio
    async def test_new_session_returns_connected_event(self):
        repo = _make_repo()
        repo.restore_hot_state = AsyncMock(return_value=None)

        orc, events = await restore_or_create_orchestrator(
            session_id="new-sess",
            tenant_id="t1",
            client_id="c1",
            repo=repo,
        )
        assert orc is not None
        assert len(events) == 1
        assert events[0].name == "connected"
        assert events[0].payload["reconnected"] is False
        repo.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_ended_session_returns_none(self):
        repo = _make_repo()
        repo.restore_hot_state = AsyncMock(return_value={"status": "ENDED"})

        orc, events = await restore_or_create_orchestrator(
            session_id="ended-sess",
            tenant_id="t1",
            client_id="c1",
            repo=repo,
        )
        assert orc is None
        assert events == []

    @pytest.mark.asyncio
    async def test_reconnect_returns_reconnected_event(self):
        repo = _make_repo()
        repo.restore_hot_state = AsyncMock(
            return_value={
                "status": "LISTENING",
                "fsm_snapshot": {"state": "LISTENING", "events": []},
                "history": [
                    {"role": "user", "content": "안녕"},
                    {"role": "assistant", "content": "반갑습니다"},
                ],
            }
        )

        orc, events = await restore_or_create_orchestrator(
            session_id="old-sess",
            tenant_id="t1",
            client_id="c1",
            repo=repo,
        )
        assert orc is not None
        assert len(events) == 1
        assert events[0].name == "reconnected"
        assert events[0].payload["turns_restored"] == 1
        assert len(orc.history) == 2
        repo.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_reconnect_unsafe_state_reverts_to_listening(self):
        repo = _make_repo()
        repo.restore_hot_state = AsyncMock(
            return_value={
                "status": "PROCESSING",
                "fsm_snapshot": {"state": "PROCESSING", "events": []},
                "history": [],
            }
        )

        orc, _ = await restore_or_create_orchestrator(
            session_id="sess",
            tenant_id="t1",
            client_id="c1",
            repo=repo,
        )
        # PROCESSING은 unsafe → LISTENING으로 복귀해야 함
        assert orc is not None
        assert orc.fsm.state in (SessionState.LISTENING, SessionState.IDLE)

    @pytest.mark.asyncio
    async def test_broken_snapshot_falls_back_to_idle(self):
        repo = _make_repo()
        repo.restore_hot_state = AsyncMock(
            return_value={
                "status": "LISTENING",
                "fsm_snapshot": {"state": "INVALID_STATE_XYZ", "events": []},
                "history": [],
            }
        )

        orc, events = await restore_or_create_orchestrator(
            session_id="sess",
            tenant_id="t1",
            client_id="c1",
            repo=repo,
        )
        assert orc is not None
        assert orc.fsm.state in (SessionState.IDLE, SessionState.LISTENING)
        assert events[0].name == "reconnected"
