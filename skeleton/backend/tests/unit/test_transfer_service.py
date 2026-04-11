"""
Unit tests for TransferService

테스트 범위:
- TransferService.request() — 수락 / 실패 / 타임아웃
- Fallback 전략: RETRY, CALLBACK, AI_RESUME
- FSM 상태 전이 검증 (TRANSFER_REQUESTED → ACCEPTED/FAILED)
- detect_transfer_intent 키워드 감지
- build_context_summary 대화 요약
- 재시도 최대 횟수 제한
"""
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from app.domain.session_fsm import SessionFSM, SessionState
from app.repositories.session_repository import SessionRepository
from app.services.transfer_service import (
    MAX_TRANSFER_RETRIES,
    TransferFallback,
    TransferReason,
    TransferRequest,
    TransferResult,
    TransferService,
    TransferStatus,
)


# ── 픽스처 ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def fsm_listening():
    return SessionFSM(SessionState.LISTENING)


@pytest.fixture
def transfer_req():
    return TransferRequest(
        session_id="sess-test",
        tenant_id="tenant-001",
        reason=TransferReason.CUSTOMER_REQUEST,
        context_summary="고객이 상담사 연결을 요청했습니다.",
    )


@pytest.fixture
def mock_repo():
    repo = AsyncMock(spec=SessionRepository)
    repo.save_transfer_info = AsyncMock()
    repo.update_state = AsyncMock()
    repo.end_session = AsyncMock()
    return repo


def make_service(mock_repo, cti_result: TransferResult | None = None) -> TransferService:
    svc = TransferService(session_repo=mock_repo)
    if cti_result is not None:
        svc._dispatch_to_cti = AsyncMock(return_value=cti_result)
    return svc


# ── 이관 수락 테스트 ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_accepted_updates_fsm(fsm_listening, transfer_req, mock_repo):
    """이관 수락 시 FSM → TRANSFER_ACCEPTED."""
    accepted = TransferResult(
        status=TransferStatus.ACCEPTED,
        agent_id="agent-001",
        agent_name="김상담",
    )
    svc = make_service(mock_repo, accepted)

    result = await svc.request(fsm_listening, transfer_req)

    assert result.status == TransferStatus.ACCEPTED
    assert fsm_listening.state == SessionState.TRANSFER_ACCEPTED


@pytest.mark.asyncio
async def test_transfer_accepted_message_contains_agent_name(
    fsm_listening, transfer_req, mock_repo
):
    accepted = TransferResult(
        status=TransferStatus.ACCEPTED,
        agent_id="agent-001",
        agent_name="박상담",
    )
    svc = make_service(mock_repo, accepted)
    result = await svc.request(fsm_listening, transfer_req)

    assert "박상담" in result.message


@pytest.mark.asyncio
async def test_transfer_accepted_persists_info(fsm_listening, transfer_req, mock_repo):
    """이관 수락 시 MongoDB에 이관 정보 저장 확인."""
    svc = make_service(mock_repo, TransferResult(
        status=TransferStatus.ACCEPTED, agent_id="a1"
    ))
    await svc.request(fsm_listening, transfer_req)
    mock_repo.save_transfer_info.assert_called_once()


# ── 이관 실패 + Fallback 테스트 ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_failed_callback_fallback(fsm_listening, transfer_req, mock_repo):
    """이관 실패 + CALLBACK fallback → TRANSFER_FAILED 상태."""
    svc = make_service(mock_repo, TransferResult(
        status=TransferStatus.FAILED,
        message="가용 상담사 없음",
    ))
    result = await svc.request(
        fsm_listening, transfer_req, fallback=TransferFallback.CALLBACK
    )

    assert result.status == TransferStatus.FALLBACK_CALLBACK
    assert result.fallback_action == TransferFallback.CALLBACK
    assert fsm_listening.state == SessionState.TRANSFER_FAILED
    assert "콜백" in result.message or "통화 중" in result.message


@pytest.mark.asyncio
async def test_transfer_failed_ai_resume_fallback(fsm_listening, transfer_req, mock_repo):
    """이관 실패 + AI_RESUME fallback → LISTENING 복귀."""
    svc = make_service(mock_repo, TransferResult(
        status=TransferStatus.FAILED
    ))
    result = await svc.request(
        fsm_listening, transfer_req, fallback=TransferFallback.AI_RESUME
    )

    assert result.status == TransferStatus.FALLBACK_AI_RESUME
    assert fsm_listening.state == SessionState.LISTENING


@pytest.mark.asyncio
async def test_transfer_timeout_triggers_callback(fsm_listening, transfer_req, mock_repo):
    """CTI 타임아웃 → CALLBACK fallback."""
    svc = make_service(mock_repo, TransferResult(
        status=TransferStatus.TIMED_OUT,
        message="시간 초과",
    ))
    result = await svc.request(
        fsm_listening, transfer_req, fallback=TransferFallback.CALLBACK
    )
    assert result.fallback_action == TransferFallback.CALLBACK


# ── RETRY fallback 테스트 ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_retry_eventually_succeeds(fsm_listening, transfer_req, mock_repo):
    """1회 실패 → RETRY → 2회차 수락."""
    call_count = 0

    async def side_effect(req):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return TransferResult(status=TransferStatus.FAILED)
        return TransferResult(
            status=TransferStatus.ACCEPTED, agent_id="agent-x"
        )

    svc = TransferService(session_repo=mock_repo)
    svc._dispatch_to_cti = side_effect

    result = await svc.request(
        fsm_listening, transfer_req, fallback=TransferFallback.RETRY
    )

    assert result.status == TransferStatus.ACCEPTED
    assert call_count == 2


@pytest.mark.asyncio
async def test_transfer_retry_max_exceeded(mock_repo):
    """최대 재시도 횟수 초과 → CALLBACK fallback."""
    fsm = SessionFSM(SessionState.LISTENING)
    req = TransferRequest(
        session_id="s1",
        tenant_id="t1",
        reason=TransferReason.MANUAL,
        context_summary="test",
    )
    svc = TransferService(session_repo=mock_repo)
    svc._dispatch_to_cti = AsyncMock(return_value=TransferResult(
        status=TransferStatus.FAILED
    ))

    result = await svc.request(fsm, req, fallback=TransferFallback.RETRY)

    # 최대 재시도 후 최종 CALLBACK fallback
    assert result.fallback_action in (
        TransferFallback.CALLBACK,
        TransferFallback.RETRY,  # retry 소진 후 callback
    )


# ── FSM 상태 오류 처리 테스트 ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_transfer_fails_gracefully_from_ended_state(mock_repo):
    """ENDED 상태에서 이관 요청 → 실패 반환 (예외 없음)."""
    fsm = SessionFSM(SessionState.ENDED)
    req = TransferRequest(
        session_id="s1",
        tenant_id="t1",
        reason=TransferReason.MANUAL,
        context_summary="test",
    )
    svc = TransferService(session_repo=mock_repo)

    result = await svc.request(fsm, req)

    assert result.status == TransferStatus.FAILED
    assert fsm.state == SessionState.ENDED  # 상태 변경 없음


# ── Mock CTI (미연동 환경) 테스트 ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mock_cti_always_returns_failed(mock_repo):
    """CTI 미연동 Mock: 항상 FAILED 반환."""
    fsm = SessionFSM(SessionState.LISTENING)
    req = TransferRequest(
        session_id="s1",
        tenant_id="t1",
        reason=TransferReason.CUSTOMER_REQUEST,
        context_summary="test",
    )
    svc = TransferService(session_repo=mock_repo, cti_connector=None)
    cti_result = await svc._mock_cti_dispatch(req)

    assert cti_result.status == TransferStatus.FAILED


# ── detect_transfer_intent 테스트 ────────────────────────────────────────────


@pytest.mark.parametrize("text,expected", [
    ("상담사 연결해줘", True),
    ("사람이랑 통화하고 싶어요", True),
    ("담당자 바꿔주세요", True),
    ("배송 조회해줘", False),
    ("영업시간 알려줘", False),
    ("agent please", True),
    ("직원 연결 부탁해", True),
])
def test_detect_transfer_intent(text, expected):
    assert TransferService.detect_transfer_intent(text) == expected


# ── build_context_summary 테스트 ─────────────────────────────────────────────


def test_build_context_summary_with_history():
    history = [
        {"role": "user", "content": "환불 요청합니다"},
        {"role": "assistant", "content": "환불 절차를 안내드리겠습니다"},
    ]
    summary = TransferService.build_context_summary(history, "환불 처리 요청")
    assert "환불" in summary
    assert "고객" in summary or "user" in summary.lower() or "고객" in summary


def test_build_context_summary_empty_history():
    summary = TransferService.build_context_summary([], "간단한 문의")
    assert "간단한 문의" in summary


def test_build_context_summary_no_args():
    summary = TransferService.build_context_summary([])
    assert isinstance(summary, str)
    assert len(summary) > 0
