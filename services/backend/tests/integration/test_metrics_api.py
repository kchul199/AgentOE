"""
Integration tests — Metrics API 엔드포인트

테스트 범위:
  - GET /api/v1/metrics/pipeline  — P50/P95 레이턴시 + budget compliance
  - GET /api/v1/metrics/sessions  — 활성 세션 게이지
  - GET /api/v1/metrics/ai        — Circuit Breaker 상태
  - GET /api/v1/metrics/transfers — 이관 통계
  - GET /api/v1/metrics/summary   — 통합 뷰
  - GET /api/v1/metrics/prometheus — Prometheus text 포맷
  - 인증 없을 때 401 반환 검증 (/prometheus 제외)
  - record_pipeline_call() 후 조회 시 반영 확인
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, MagicMock, patch

# 외부 의존성 mock (DB/Redis 없이 실행)
import sys
import unittest.mock as mock

for mod in [
    "motor", "motor.motor_asyncio",
    "pymongo", "pymongo.errors",
    "redis", "redis.asyncio",
    "groq", "google.cloud", "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1",
    "grpc",
]:
    if mod not in sys.modules:
        sys.modules[mod] = mock.MagicMock()

from app.core.metrics import (
    _store,
    _MetricsStore,
    record_pipeline_call,
    record_transfer_request,
    inc_active_sessions,
    dec_active_sessions,
    get_pipeline_stats,
    generate_prometheus_metrics,
)
from app.core.auth import create_access_token


# ── 픽스처 ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_metrics():
    """각 테스트 전 메트릭 스토어 초기화."""
    import app.core.metrics as m
    m._store = _MetricsStore()
    yield
    m._store = _MetricsStore()


@pytest.fixture
def client():
    """FastAPI TestClient — DB/Redis lifespan을 mock으로 대체."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis_client.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis_client.close_redis", new_callable=AsyncMock), \
         patch("app.core.redis_client.get_redis", return_value=AsyncMock()), \
         patch("app.domain.kill_switch.KillSwitchService.is_active",
               new_callable=AsyncMock, return_value=False):
        from app.main import app
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c


@pytest.fixture
def auth_headers():
    token = create_access_token("tenant-001", "client-001", ["operator"])
    return {"Authorization": f"Bearer {token}"}


# ── 인증 검사 ─────────────────────────────────────────────────────────────────


def test_pipeline_metrics_requires_auth(client):
    resp = client.get("/api/v1/metrics/pipeline")
    assert resp.status_code == 403


def test_sessions_metrics_requires_auth(client):
    resp = client.get("/api/v1/metrics/sessions")
    assert resp.status_code == 403


def test_prometheus_no_auth_required(client):
    """Prometheus 엔드포인트는 인증 불필요."""
    resp = client.get("/api/v1/metrics/prometheus")
    assert resp.status_code == 200


# ── pipeline 메트릭 ───────────────────────────────────────────────────────────


def test_pipeline_metrics_empty(client, auth_headers):
    resp = client.get("/api/v1/metrics/pipeline", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "tenant_id" in data
    assert data["tenant_id"] == "tenant-001"
    assert "pipeline" in data
    assert "budget_compliance" in data


def test_pipeline_metrics_after_recording(client, auth_headers):
    """record_pipeline_call() 후 API 응답에 반영되는지 검증."""
    record_pipeline_call(
        tenant_id="tenant-001",
        success=True,
        total_ms=800.0,
        stt_ms=200.0,
        llm_ms=400.0,
        tts_ms=150.0,
    )
    record_pipeline_call(
        tenant_id="tenant-001",
        success=False,
        total_ms=3000.0,
        stt_ms=600.0,
    )

    resp = client.get("/api/v1/metrics/pipeline", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()

    calls = data["pipeline"]["calls"]
    assert calls["total"] == 2
    assert calls["success"] == 1
    assert calls["error"] == 1
    assert data["pipeline"]["error_rate"] == 0.5

    # 레이턴시 통계 존재 확인
    lat = data["pipeline"]["pipeline_latency_ms"]
    assert lat["count"] == 2
    assert lat["p50"] > 0
    assert lat["p95"] > 0


def test_pipeline_budget_compliance(client, auth_headers):
    """P95 레이턴시 예산 준수 여부 계산 검증."""
    # 모두 예산 내
    for _ in range(20):
        record_pipeline_call(
            tenant_id="tenant-001", success=True,
            total_ms=1000.0, stt_ms=200.0, llm_ms=300.0, tts_ms=100.0,
        )

    resp = client.get("/api/v1/metrics/pipeline", headers=auth_headers)
    compliance = resp.json()["budget_compliance"]
    assert compliance["stt_p95_ok"] is True
    assert compliance["total_p95_ok"] is True


def test_pipeline_budget_violation(client, auth_headers):
    """P95가 예산 초과하면 compliance False."""
    for _ in range(20):
        record_pipeline_call(
            tenant_id="tenant-001", success=True,
            total_ms=5000.0, stt_ms=800.0,  # STT 예산(500ms) 초과
        )

    resp = client.get("/api/v1/metrics/pipeline", headers=auth_headers)
    compliance = resp.json()["budget_compliance"]
    assert compliance["stt_p95_ok"] is False
    assert compliance["total_p95_ok"] is False


# ── session 메트릭 ────────────────────────────────────────────────────────────


def test_session_metrics_active_count(client, auth_headers):
    inc_active_sessions("tenant-001")
    inc_active_sessions("tenant-001")
    dec_active_sessions("tenant-001")

    resp = client.get("/api/v1/metrics/sessions", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["active_sessions"] == 1


# ── ai (Circuit Breaker) 메트릭 ───────────────────────────────────────────────


def test_ai_metrics_returns_circuit_breakers(client, auth_headers):
    resp = client.get("/api/v1/metrics/ai", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "circuit_breakers" in data
    assert "vendor_latency_ms" in data
    assert "latency_budget_ms" in data
    assert isinstance(data["circuit_breakers"], list)


# ── transfer 메트릭 ───────────────────────────────────────────────────────────


def test_transfer_metrics_empty(client, auth_headers):
    resp = client.get("/api/v1/metrics/transfers", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_transfers"] == 0


def test_transfer_metrics_after_recording(client, auth_headers):
    record_transfer_request("tenant-001", "CUSTOMER_REQUEST")
    record_transfer_request("tenant-001", "CUSTOMER_REQUEST")
    record_transfer_request("tenant-001", "G4_POLICY")

    resp = client.get("/api/v1/metrics/transfers", headers=auth_headers)
    data = resp.json()
    assert data["total_transfers"] == 3
    assert data["by_reason"]["CUSTOMER_REQUEST"] == 2.0
    assert data["by_reason"]["G4_POLICY"] == 1.0


# ── summary 메트릭 ────────────────────────────────────────────────────────────


def test_summary_metrics_structure(client, auth_headers):
    resp = client.get("/api/v1/metrics/summary", headers=auth_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert "tenant_id" in data
    assert "pipeline" in data
    assert "circuit_breakers" in data
    assert "active_sessions" in data
    assert "transfers" in data


# ── Prometheus 포맷 ───────────────────────────────────────────────────────────


def test_prometheus_format_has_required_metrics(client):
    record_pipeline_call("tenant-001", True, 500.0, stt_ms=100.0)
    inc_active_sessions("tenant-001")

    resp = client.get("/api/v1/metrics/prometheus")
    assert resp.status_code == 200
    body = resp.text

    assert "agentoe_pipeline_calls_total" in body
    assert "agentoe_pipeline_latency_ms" in body
    assert "agentoe_active_sessions" in body


def test_prometheus_content_type(client):
    resp = client.get("/api/v1/metrics/prometheus")
    assert "text/plain" in resp.headers.get("content-type", "")


# ── X-Request-ID 헤더 주입 ────────────────────────────────────────────────────


def test_request_id_header_injected(client, auth_headers):
    """LoggingMiddleware가 X-Request-ID를 응답 헤더에 주입하는지 확인."""
    resp = client.get("/api/v1/metrics/pipeline", headers=auth_headers)
    assert "x-request-id" in resp.headers


def test_custom_request_id_preserved(client, auth_headers):
    """클라이언트가 보낸 X-Request-ID가 응답에 그대로 반환되는지 확인."""
    custom_id = "my-req-abc123"
    headers = {**auth_headers, "X-Request-ID": custom_id}
    resp = client.get("/api/v1/metrics/pipeline", headers=headers)
    assert resp.headers.get("x-request-id") == custom_id
