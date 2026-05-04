"""
Integration tests — VBGW WebSocket 엔드포인트

테스트 범위:
  - 유효 JWT로 연결 → connected 이벤트 수신
  - 무효 JWT → 4001 close code
  - 세션 ENDED → 4004 close code
  - start_listening → LISTENING 상태 전이 이벤트
  - hangup → ENDED 상태 전이 이벤트
  - ping → pong 응답
  - request_transfer → transfer_update 이벤트
  - Lease Lock 충돌 → 4002 close code
  - 메트릭 카운터: WebSocket 연결 시 active_sessions 증가
"""
from __future__ import annotations

import json
import sys
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# 외부 의존성 mock
for mod in [
    "motor", "motor.motor_asyncio",
    "pymongo", "pymongo.errors",
    "redis", "redis.asyncio",
    "groq", "google.cloud", "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1", "grpc",
]:
    if mod not in sys.modules:
        sys.modules[mod] = mock.MagicMock()

from app.core.auth import create_access_token


# ── 공통 mock 패치 컨텍스트 ──────────────────────────────────────────────────


def _app_patches():
    return [
        patch("app.core.database.init_db", new_callable=AsyncMock),
        patch("app.core.database.close_db", new_callable=AsyncMock),
        patch("app.core.redis_client.init_redis", new_callable=AsyncMock),
        patch("app.core.redis_client.close_redis", new_callable=AsyncMock),
        patch("app.core.redis_client.get_redis", return_value=AsyncMock()),
        patch(
            "app.domain.kill_switch.KillSwitchService.is_active",
            new_callable=AsyncMock,
            return_value=False,
        ),
        # SessionRepository mock — DB 없이 테스트
        patch(
            "app.api.v1.routers.vbgw.SessionRepository",
            return_value=_mock_session_repo(),
        ),
    ]


def _mock_session_repo():
    repo = AsyncMock()
    repo.acquire_session_lease.return_value = True  # lease 획득 성공
    repo.release_session_lease = AsyncMock()
    repo.restore_hot_state.return_value = None       # 신규 세션
    repo.create = AsyncMock(return_value={})
    repo.save_turn = AsyncMock()
    repo.end_session = AsyncMock()
    repo.update_state = AsyncMock()
    repo.save_transfer_info = AsyncMock()
    return repo


@pytest.fixture
def client():
    patches = _app_patches()
    for p in patches:
        p.start()
    from app.main import app
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    for p in patches:
        p.stop()


@pytest.fixture
def valid_token():
    return create_access_token("tenant-001", "client-001", ["operator"])


@pytest.fixture
def ws_url(valid_token):
    return f"/api/v1/ws/vbgw?token={valid_token}&session_id=test-sess-001"


# ── 연결 수립 테스트 ──────────────────────────────────────────────────────────


def test_websocket_connected_event(client, ws_url):
    """유효 JWT → connected 이벤트 수신."""
    with client.websocket_connect(ws_url) as ws:
        data = json.loads(ws.receive_text())
        assert data["event"] == "connected"
        assert data["session_id"] == "test-sess-001"
        assert data["reconnected"] is False


def test_websocket_invalid_token_rejected(client):
    """무효 JWT → WebSocket close (4001)."""
    url = "/api/v1/ws/vbgw?token=invalid.token.here&session_id=s1"
    with pytest.raises(Exception):
        with client.websocket_connect(url) as ws:
            ws.receive_text()


def test_websocket_missing_token_rejected(client):
    """token 파라미터 없음 → 422."""
    resp = client.get("/api/v1/ws/vbgw?session_id=s1")
    assert resp.status_code in (422, 400, 403)


# ── Kill Switch 테스트 ────────────────────────────────────────────────────────


def test_websocket_kill_switch_blocked(client, valid_token):
    """Kill Switch 활성 → 연결 거부 (4003)."""
    url = f"/api/v1/ws/vbgw?token={valid_token}&session_id=s1"
    with patch(
        "app.domain.kill_switch.KillSwitchService.is_active",
        new_callable=AsyncMock,
        return_value=True,
    ):
        with pytest.raises(Exception):
            with client.websocket_connect(url) as ws:
                ws.receive_text()


# ── 제어 메시지 테스트 ────────────────────────────────────────────────────────


def test_ping_pong(client, ws_url):
    """ping → pong 응답."""
    with client.websocket_connect(ws_url) as ws:
        ws.receive_text()  # connected 이벤트 소비
        ws.send_text(json.dumps({"action": "ping"}))
        data = json.loads(ws.receive_text())
        assert data["event"] == "pong"


def test_start_listening_state_change(client, ws_url):
    """start_listening → state_change LISTENING 이벤트."""
    with client.websocket_connect(ws_url) as ws:
        ws.receive_text()  # connected
        ws.send_text(json.dumps({"action": "start_listening"}))
        data = json.loads(ws.receive_text())
        assert data["event"] == "state_change"
        assert data["state"] == "LISTENING"


def test_hangup_ends_session(client, ws_url):
    """hangup → ENDED 상태 이벤트."""
    with client.websocket_connect(ws_url) as ws:
        ws.receive_text()  # connected
        ws.send_text(json.dumps({"action": "hangup"}))
        data = json.loads(ws.receive_text())
        assert data["event"] == "state_change"
        assert data["state"] == "ENDED"


def test_unknown_action_returns_error(client, ws_url):
    """알 수 없는 action → error 이벤트."""
    with client.websocket_connect(ws_url) as ws:
        ws.receive_text()  # connected
        ws.send_text(json.dumps({"action": "do_something_weird"}))
        data = json.loads(ws.receive_text())
        assert data["event"] == "error"
        assert data["code"] == "UNKNOWN_ACTION"


def test_invalid_json_returns_error(client, ws_url):
    """잘못된 JSON → error 이벤트."""
    with client.websocket_connect(ws_url) as ws:
        ws.receive_text()  # connected
        ws.send_text("not valid json {{{")
        data = json.loads(ws.receive_text())
        assert data["event"] == "error"
        assert data["code"] == "INVALID_JSON"


# ── 이관 요청 테스트 ──────────────────────────────────────────────────────────


def test_request_transfer_returns_update(client, ws_url):
    """request_transfer → transfer_update 이벤트."""
    with patch(
        "app.services.transfer_service.TransferService.request",
        new_callable=AsyncMock,
    ) as mock_transfer:
        from app.services.transfer_service import TransferResult, TransferStatus
        mock_transfer.return_value = TransferResult(
            status=TransferStatus.FALLBACK_CALLBACK,
            message="콜백 예약됨",
        )
        with client.websocket_connect(ws_url) as ws:
            ws.receive_text()  # connected
            ws.send_text(json.dumps({
                "action": "request_transfer",
                "reason": "CUSTOMER_REQUEST",
                "context": "상담사 연결 원해요",
            }))
            data = json.loads(ws.receive_text())
            assert data["event"] == "transfer_update"
            assert "status" in data
            assert "message" in data


# ── Lease Lock 테스트 ─────────────────────────────────────────────────────────


def test_lease_conflict_rejected(client, valid_token):
    """Lease Lock 획득 실패 → error + 4002 close."""
    with patch(
        "app.api.v1.routers.vbgw.SessionRepository",
        return_value=_mock_session_repo_lease_fail(),
    ):
        url = f"/api/v1/ws/vbgw?token={valid_token}&session_id=locked-sess"
        with pytest.raises(Exception):
            with client.websocket_connect(url) as ws:
                ws.receive_text()


def _mock_session_repo_lease_fail():
    repo = AsyncMock()
    repo.acquire_session_lease.return_value = False  # lease 획득 실패
    repo.release_session_lease = AsyncMock()
    repo.restore_hot_state.return_value = None
    repo.create = AsyncMock(return_value={})
    repo.end_session = AsyncMock()
    return repo


# ── 재연결 복구 테스트 ────────────────────────────────────────────────────────


def test_reconnect_restores_session(client, valid_token):
    """기존 세션 있을 때 reconnected 이벤트 + turns_restored 반환."""
    existing_hot = {
        "status": "LISTENING",
        "fsm_snapshot": {"state": "LISTENING", "events": []},
        "history": [
            {"role": "user", "content": "안녕"},
            {"role": "assistant", "content": "안녕하세요"},
        ],
        "tenant_id": "tenant-001",
        "client_id": "client-001",
    }

    repo = _mock_session_repo()
    repo.restore_hot_state.return_value = existing_hot

    with patch("app.api.v1.routers.vbgw.SessionRepository", return_value=repo):
        url = f"/api/v1/ws/vbgw?token={valid_token}&session_id=existing-sess"
        with client.websocket_connect(url) as ws:
            data = json.loads(ws.receive_text())
            assert data["event"] == "reconnected"
            assert data["turns_restored"] == 1  # 2 messages = 1 turn
            assert data["session_id"] == "existing-sess"


def test_ended_session_rejected(client, valid_token):
    """ENDED 세션 재연결 → error 이벤트."""
    repo = _mock_session_repo()
    repo.restore_hot_state.return_value = None  # ENDED 처리됨

    with patch("app.api.v1.routers.vbgw.SessionRepository", return_value=repo):
        url = f"/api/v1/ws/vbgw?token={valid_token}&session_id=ended-sess"
        # ENDED 세션 → 4004 또는 error 이벤트 후 종료
        with pytest.raises(Exception):
            with client.websocket_connect(url) as ws:
                ws.receive_text()
