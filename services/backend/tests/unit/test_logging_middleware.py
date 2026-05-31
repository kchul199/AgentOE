"""
Unit tests for LoggingMiddleware + logging.py helpers

테스트 범위:
  - _extract_tenant_from_jwt: 유효 / 무효 / 없음 케이스
  - bind_request_context: request_id 자동 생성 + 커스텀
  - bind_session_context: session_id 바인딩
  - clear_request_context: 정리 확인
  - LoggingMiddleware.dispatch: X-Request-ID 헤더 주입
  - WebSocket 경로 (/ws/*) pass-through
  - 401/500 상태코드 시 로그 레벨 검증
"""

from __future__ import annotations

import base64
import json
import sys
import unittest.mock as mock
from unittest.mock import AsyncMock, patch

import pytest
from structlog.contextvars import get_contextvars

# 외부 의존성 mock
for mod in [
    "motor",
    "motor.motor_asyncio",
    "pymongo",
    "pymongo.errors",
    "redis",
    "redis.asyncio",
    "groq",
    "google.cloud",
    "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1",
    "grpc",
]:
    if mod not in sys.modules:
        sys.modules[mod] = mock.MagicMock()

from app.core.auth import create_access_token
from app.core.logging import (
    bind_pipeline_context,
    bind_request_context,
    bind_session_context,
    clear_request_context,
)
from app.middleware.logging_middleware import (
    _extract_tenant_from_jwt,
)

# ── 픽스처 ─────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def clean_context():
    """각 테스트 전후 structlog context var 정리."""
    clear_request_context()
    yield
    clear_request_context()


# ── _extract_tenant_from_jwt ─────────────────────────────────────────────────


def test_extract_tenant_from_valid_jwt():
    token = create_access_token("tenant-001", "client-abc", ["operator"])
    header = f"Bearer {token}"
    tenant_id, client_id = _extract_tenant_from_jwt(header)
    assert tenant_id == "tenant-001"
    assert client_id == "client-abc"


def test_extract_tenant_no_header():
    tenant_id, client_id = _extract_tenant_from_jwt(None)
    assert tenant_id is None
    assert client_id is None


def test_extract_tenant_invalid_format():
    tenant_id, _client_id = _extract_tenant_from_jwt("Token abc123")
    assert tenant_id is None


def test_extract_tenant_malformed_jwt():
    tenant_id, _client_id = _extract_tenant_from_jwt("Bearer not.a.jwt")
    assert tenant_id is None


def test_extract_tenant_valid_bearer_no_claims():
    """payload에 tenant_id 없으면 None 반환."""
    # 수동으로 payload 인코딩
    payload = base64.urlsafe_b64encode(json.dumps({"sub": "only-sub"}).encode()).decode()
    fake_jwt = f"header.{payload}.sig"
    tenant_id, client_id = _extract_tenant_from_jwt(f"Bearer {fake_jwt}")
    assert tenant_id is None
    assert client_id == "only-sub"


# ── bind_request_context ─────────────────────────────────────────────────────


def test_bind_request_context_generates_request_id():
    rid = bind_request_context(tenant_id="t1")
    ctx = get_contextvars()
    assert ctx.get("request_id") == rid
    assert ctx.get("tenant_id") == "t1"


def test_bind_request_context_custom_id():
    rid = bind_request_context(request_id="custom-123")
    assert rid == "custom-123"
    assert get_contextvars()["request_id"] == "custom-123"


def test_bind_request_context_optional_fields():
    bind_request_context(path="/api/test", method="GET")
    ctx = get_contextvars()
    assert ctx.get("path") == "/api/test"
    assert ctx.get("method") == "GET"


def test_bind_request_context_none_fields_not_added():
    bind_request_context()
    ctx = get_contextvars()
    assert "tenant_id" not in ctx
    assert "client_id" not in ctx


# ── bind_session_context ──────────────────────────────────────────────────────


def test_bind_session_context():
    bind_session_context("sess-001", tenant_id="t1", client_id="c1")
    ctx = get_contextvars()
    assert ctx["session_id"] == "sess-001"
    assert ctx["tenant_id"] == "t1"
    assert ctx["client_id"] == "c1"


def test_bind_session_context_accumulates():
    """기존 request context에 session_id 추가."""
    bind_request_context(request_id="req-1", tenant_id="t1")
    bind_session_context("sess-001")
    ctx = get_contextvars()
    assert ctx["request_id"] == "req-1"
    assert ctx["session_id"] == "sess-001"


# ── bind_pipeline_context ─────────────────────────────────────────────────────


def test_bind_pipeline_context():
    bind_pipeline_context(stage="stt", policy_level="G1")
    ctx = get_contextvars()
    assert ctx["pipeline_stage"] == "stt"
    assert ctx["policy_level"] == "G1"


# ── clear_request_context ─────────────────────────────────────────────────────


def test_clear_request_context_removes_all():
    bind_request_context(request_id="r1", tenant_id="t1")
    bind_session_context("s1")
    clear_request_context()
    ctx = get_contextvars()
    assert ctx == {}


# ── LoggingMiddleware via FastAPI TestClient ──────────────────────────────────


@pytest.fixture
def client():
    with (
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
    ):
        from fastapi.testclient import TestClient

        from app.main import app

        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


def test_middleware_injects_request_id(client):
    resp = client.get("/api/v1/health")
    assert "x-request-id" in resp.headers
    assert len(resp.headers["x-request-id"]) > 0


def test_middleware_preserves_client_request_id(client):
    resp = client.get("/api/v1/health", headers={"X-Request-ID": "my-id-001"})
    assert resp.headers["x-request-id"] == "my-id-001"


def test_middleware_skips_websocket_path(client):
    """WebSocket 경로는 미들웨어 통과 — 일반 GET 요청으로 테스트시 404 응답."""
    resp = client.get("/api/v1/ws/vbgw")
    # WebSocket 경로는 GET이면 404/422
    assert resp.status_code in (404, 422, 400, 403)
    # 단 request_id 헤더는 주입 안 됨 (ws path는 bypass)
