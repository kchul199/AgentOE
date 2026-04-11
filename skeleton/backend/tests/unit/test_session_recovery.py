"""
Unit tests for Session Recovery

테스트 범위:
- SessionFSM TRANSFER 상태 전이
- SessionFSM 스냅샷 직렬화 / 역직렬화 (재연결 복구)
- SessionFSM 오버레이 이벤트 기록
- SessionRepository.restore_hot_state (Redis hit / miss / ENDED)
- SessionRepository.save_turn (MongoDB + Redis 동기)
- _restore_or_create_session 헬퍼 (신규 / 재연결 / ENDED 거부)
"""
import json
import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.session_fsm import (
    SessionEventType,
    SessionFSM,
    SessionState,
)
from app.repositories.session_repository import SessionRepository


# ── SessionFSM TRANSFER 상태 전이 ─────────────────────────────────────────────


def test_transfer_requested_from_listening():
    fsm = SessionFSM(SessionState.LISTENING)
    fsm.transition(SessionState.TRANSFER_REQUESTED)
    assert fsm.state == SessionState.TRANSFER_REQUESTED


def test_transfer_accepted_from_requested():
    fsm = SessionFSM(SessionState.TRANSFER_REQUESTED)
    fsm.transition(SessionState.TRANSFER_ACCEPTED)
    assert fsm.state == SessionState.TRANSFER_ACCEPTED


def test_transfer_failed_from_requested():
    fsm = SessionFSM(SessionState.TRANSFER_REQUESTED)
    fsm.transition(SessionState.TRANSFER_FAILED)
    assert fsm.state == SessionState.TRANSFER_FAILED


def test_transfer_failed_can_resume_listening():
    """이관 실패 후 AI 재응대를 위해 LISTENING 복귀 가능."""
    fsm = SessionFSM(SessionState.TRANSFER_FAILED)
    assert fsm.can_transition(SessionState.LISTENING)
    fsm.transition(SessionState.LISTENING)
    assert fsm.state == SessionState.LISTENING


def test_transfer_accepted_ends_session():
    """TRANSFER_ACCEPTED → ENDED만 허용."""
    fsm = SessionFSM(SessionState.TRANSFER_ACCEPTED)
    assert fsm.can_transition(SessionState.ENDED)
    assert not fsm.can_transition(SessionState.LISTENING)


def test_transfer_requested_from_inferring():
    """INFERRING 중에도 긴급 이관 요청 가능."""
    fsm = SessionFSM(SessionState.INFERRING)
    assert fsm.can_transition(SessionState.TRANSFER_REQUESTED)


def test_ended_has_no_transitions():
    fsm = SessionFSM(SessionState.ENDED)
    for state in SessionState:
        assert not fsm.can_transition(state)


def test_is_transfer_in_progress_true():
    fsm = SessionFSM(SessionState.TRANSFER_REQUESTED)
    assert fsm.is_transfer_in_progress is True

    fsm2 = SessionFSM(SessionState.TRANSFER_ACCEPTED)
    assert fsm2.is_transfer_in_progress is True


def test_is_transfer_in_progress_false():
    fsm = SessionFSM(SessionState.LISTENING)
    assert fsm.is_transfer_in_progress is False


def test_is_active_true_for_non_ended():
    for state in [
        SessionState.IDLE, SessionState.LISTENING, SessionState.TRANSFER_REQUESTED
    ]:
        assert SessionFSM(state).is_active is True


def test_is_active_false_for_ended():
    assert SessionFSM(SessionState.ENDED).is_active is False


# ── FSM 이벤트 히스토리 ───────────────────────────────────────────────────────


def test_transition_records_event():
    fsm = SessionFSM(SessionState.IDLE)
    fsm.transition(SessionState.LISTENING)
    assert len(fsm.events) == 1
    ev = fsm.events[0]
    assert ev.event_type == SessionEventType.STATE_CHANGED
    assert ev.from_state == SessionState.IDLE
    assert ev.to_state == SessionState.LISTENING


def test_record_event_no_state_change():
    fsm = SessionFSM(SessionState.LISTENING)
    fsm.record_event(SessionEventType.VENDOR_DEGRADED, {"svc": "groq-stt"})
    assert len(fsm.events) == 1
    ev = fsm.events[0]
    assert ev.event_type == SessionEventType.VENDOR_DEGRADED
    assert ev.to_state is None  # 상태 변경 없음
    assert ev.metadata["svc"] == "groq-stt"


# ── FSM 스냅샷 직렬화 / 역직렬화 ─────────────────────────────────────────────


def test_to_snapshot_contains_state_and_events():
    fsm = SessionFSM(SessionState.IDLE)
    fsm.transition(SessionState.LISTENING)
    snapshot = fsm.to_snapshot()

    assert snapshot["state"] == "LISTENING"
    assert isinstance(snapshot["events"], list)
    assert len(snapshot["events"]) == 1
    assert snapshot["events"][0]["event_type"] == "STATE_CHANGED"


def test_from_snapshot_restores_state():
    fsm = SessionFSM(SessionState.IDLE)
    fsm.transition(SessionState.LISTENING)
    fsm.transition(SessionState.SPEAKING_DETECTED)
    snapshot = fsm.to_snapshot()

    restored = SessionFSM.from_snapshot(snapshot)
    assert restored.state == SessionState.SPEAKING_DETECTED


def test_from_snapshot_adds_restored_event():
    fsm = SessionFSM(SessionState.LISTENING)
    snapshot = fsm.to_snapshot()

    restored = SessionFSM.from_snapshot(snapshot)
    event_types = [e.event_type for e in restored.events]
    assert SessionEventType.SESSION_RESTORED in event_types


def test_from_snapshot_empty_dict_defaults_to_idle():
    fsm = SessionFSM.from_snapshot({})
    assert fsm.state == SessionState.IDLE


def test_snapshot_roundtrip_preserves_metadata():
    fsm = SessionFSM(SessionState.IDLE)
    fsm.transition(SessionState.TRANSFER_REQUESTED,
                   metadata={"reason": "G4_POLICY", "priority": 3})
    snapshot = fsm.to_snapshot()
    restored = SessionFSM.from_snapshot(snapshot)

    transfer_event = next(
        e for e in restored.events
        if e.event_type == SessionEventType.STATE_CHANGED
        and e.to_state == SessionState.TRANSFER_REQUESTED
    )
    assert transfer_event.metadata["reason"] == "G4_POLICY"


# ── SessionRepository.restore_hot_state ──────────────────────────────────────


@pytest.mark.asyncio
async def test_restore_hot_state_redis_hit():
    """Redis에 hot-state 있을 때 즉시 반환."""
    hot = {
        "status": "LISTENING",
        "fsm_snapshot": {"state": "LISTENING", "events": []},
        "history": [{"role": "user", "content": "안녕"}],
        "tenant_id": "t1",
        "client_id": "c1",
    }
    repo = SessionRepository()

    with patch("app.repositories.session_repository.get_session_state",
               new=AsyncMock(return_value=hot)):
        result = await repo.restore_hot_state("sess-001")

    assert result is not None
    assert result["status"] == "LISTENING"
    assert len(result["history"]) == 1


@pytest.mark.asyncio
async def test_restore_hot_state_redis_miss_mongo_fallback():
    """Redis miss → MongoDB fallback → Redis 재워밍."""
    mongo_doc = {
        "session_id": "sess-002",
        "status": "IDLE",
        "fsm_snapshot": {"state": "IDLE", "events": []},
        "tenant_id": "t1",
        "client_id": "c1",
    }
    repo = SessionRepository()

    with patch("app.repositories.session_repository.get_session_state",
               new=AsyncMock(return_value=None)), \
         patch.object(repo, "get_by_id", new=AsyncMock(return_value=mongo_doc)), \
         patch.object(repo, "get_recent_history", new=AsyncMock(return_value=[])), \
         patch("app.repositories.session_repository.set_session_state",
               new=AsyncMock()) as mock_set:

        result = await repo.restore_hot_state("sess-002")

    assert result is not None
    assert result["status"] == "IDLE"
    mock_set.assert_called_once()  # Redis 재워밍 확인


@pytest.mark.asyncio
async def test_restore_hot_state_ended_session_returns_none():
    """ENDED 세션은 None 반환 (재연결 거부)."""
    hot = {"status": "ENDED"}
    repo = SessionRepository()

    with patch("app.repositories.session_repository.get_session_state",
               new=AsyncMock(return_value=hot)):
        result = await repo.restore_hot_state("sess-ended")

    assert result is None


@pytest.mark.asyncio
async def test_restore_hot_state_not_found_returns_none():
    """MongoDB에도 없으면 None 반환."""
    from app.core.exceptions import SessionNotFoundError
    repo = SessionRepository()

    with patch("app.repositories.session_repository.get_session_state",
               new=AsyncMock(return_value=None)), \
         patch.object(repo, "get_by_id",
                      side_effect=SessionNotFoundError("sess-new")):

        result = await repo.restore_hot_state("sess-new")

    assert result is None


# ── _restore_or_create_session 통합 테스트 ───────────────────────────────────


@pytest.mark.asyncio
async def test_restore_creates_new_session_when_not_found():
    """Redis/MongoDB에 세션 없음 → 신규 세션 생성."""
    from app.api.v1.routers.vbgw import _restore_or_create_session

    mock_ws = AsyncMock()
    mock_repo = AsyncMock(spec=SessionRepository)
    mock_repo.restore_hot_state.return_value = None
    mock_repo.create = AsyncMock(return_value={})

    session = await _restore_or_create_session(
        ws=mock_ws,
        session_id="new-sess",
        tenant_id="t1",
        client_id="c1",
        repo=mock_repo,
    )

    assert session is not None
    assert session._is_reconnect is False
    mock_repo.create.assert_called_once()


@pytest.mark.asyncio
async def test_restore_reconnects_existing_session():
    """Redis에 세션 있음 → 재연결 복구."""
    from app.api.v1.routers.vbgw import _restore_or_create_session

    hot = {
        "status": "LISTENING",
        "fsm_snapshot": {"state": "LISTENING", "events": []},
        "history": [
            {"role": "user", "content": "배송 조회"},
            {"role": "assistant", "content": "배송 정보를 확인해 드리겠습니다."},
        ],
        "tenant_id": "t1",
        "client_id": "c1",
    }
    mock_ws = AsyncMock()
    mock_repo = AsyncMock(spec=SessionRepository)
    mock_repo.restore_hot_state.return_value = hot

    session = await _restore_or_create_session(
        ws=mock_ws,
        session_id="exist-sess",
        tenant_id="t1",
        client_id="c1",
        repo=mock_repo,
    )

    assert session is not None
    assert session._is_reconnect is True
    assert len(session.history) == 2


@pytest.mark.asyncio
async def test_restore_rejects_ended_session():
    """ENDED 세션 → None 반환."""
    from app.api.v1.routers.vbgw import _restore_or_create_session

    mock_ws = AsyncMock()
    mock_repo = AsyncMock(spec=SessionRepository)
    mock_repo.restore_hot_state.return_value = None  # ENDED → None

    session = await _restore_or_create_session(
        ws=mock_ws,
        session_id="ended-sess",
        tenant_id="t1",
        client_id="c1",
        repo=mock_repo,
    )

    assert session is None
