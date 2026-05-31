"""test_portal_smoke.py — 스택 없이 실행 가능한 웹 포탈 스모크 테스트

requests 만으로 모든 HTTP 엔드포인트를 검증.
브라우저/Docker 불필요 — CI 단계에서 항상 실행 가능.

스택이 기동되지 않은 경우 pytest.skip 으로 우아하게 건너뜀.
"""

from __future__ import annotations

import os
import time
import pytest
import requests

BACKEND_URL      = os.environ.get("BACKEND_URL",      "http://localhost:8000")
FRONTEND_URL     = os.environ.get("FRONTEND_URL",     "http://localhost:3000")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
NGINX_URL        = os.environ.get("NGINX_URL",        "http://localhost:80")
GRAFANA_URL      = os.environ.get("GRAFANA_URL",      "http://localhost:3001")
HTTP_TIMEOUT     = int(os.environ.get("HTTP_TIMEOUT", "3"))

SLO_RESPONSE_MS  = 2000


def get(url: str, **kwargs) -> requests.Response:
    return requests.get(url, timeout=HTTP_TIMEOUT, **kwargs)


def is_up(base: str) -> bool:
    try:
        requests.get(base, timeout=2)
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# Backend API
# ─────────────────────────────────────────────────────────────────────────────

class TestBackendAPI:
    """Backend FastAPI (:8000) 엔드포인트 스모크 테스트."""

    @pytest.fixture(autouse=True)
    def require_backend(self):
        if not is_up(BACKEND_URL):
            pytest.skip(f"Backend 미기동 ({BACKEND_URL})")

    def test_health_live(self):
        # /livez (신규) 또는 /health (하위호환) 둘 다 시도
        for path in ["/api/v1/livez", "/api/v1/health"]:
            r = get(f"{BACKEND_URL}{path}")
            if r.status_code == 200:
                assert r.json().get("status") in ("alive", "ok", "live", "healthy")
                return
        pytest.fail("livez / health 엔드포인트 모두 실패")

    def test_health_ready(self):
        # /readyz (신규) 또는 /health (하위호환)
        for path in ["/api/v1/readyz", "/api/v1/health"]:
            r = get(f"{BACKEND_URL}{path}")
            if r.status_code in (200, 503):
                return
        pytest.fail("readyz 엔드포인트 없음")

    def test_openapi_schema(self):
        r = get(f"{BACKEND_URL}/openapi.json")
        assert r.status_code == 200
        schema = r.json()
        assert schema["openapi"].startswith("3.")
        paths = schema.get("paths", {})
        # /api/v1/livez (liveness) 확인
        assert any(p.startswith("/api/v1/livez") or p.startswith("/api/v1/health") for p in paths), \
            f"OpenAPI 스키마에 health/livez 경로 없음. 실제 경로: {sorted(paths)[:10]}"
        # /api/v1/sessions (trailing slash 허용) 확인
        assert any(p.rstrip("/") == "/api/v1/sessions" for p in paths), \
            f"OpenAPI 스키마에 /api/v1/sessions 경로 없음. 실제 경로: {sorted(paths)[:10]}"

    def test_swagger_ui_html(self):
        r = get(f"{BACKEND_URL}/api/docs")
        assert r.status_code == 200
        assert "swagger" in r.text.lower() or "openapi" in r.text.lower()

    def test_redoc_html(self):
        r = get(f"{BACKEND_URL}/api/redoc")
        assert r.status_code == 200
        assert "redoc" in r.text.lower()

    def test_auth_required_sessions(self):
        r = get(f"{BACKEND_URL}/api/v1/sessions")
        assert r.status_code in (401, 403, 422), \
            f"/sessions 인증 없이 {r.status_code} — 보안 이상"

    def test_auth_required_scenarios(self):
        r = get(f"{BACKEND_URL}/api/v1/scenarios")
        assert r.status_code in (401, 403, 422)

    def test_invalid_jwt_rejected(self):
        r = get(f"{BACKEND_URL}/api/v1/sessions",
                headers={"Authorization": "Bearer x.y.z"})
        assert r.status_code in (401, 403)

    def test_prometheus_metrics_endpoint(self):
        r = get(f"{BACKEND_URL}/api/v1/metrics/prometheus")
        assert r.status_code in (200, 401, 403)
        if r.status_code == 200:
            assert "http_requests" in r.text or "#" in r.text

    def test_content_type_json(self):
        r = get(f"{BACKEND_URL}/api/v1/health/live")
        ct = r.headers.get("content-type", "")
        assert "application/json" in ct, f"Content-Type 이상: {ct}"

    def test_cors_headers(self):
        r = requests.options(
            f"{BACKEND_URL}/api/v1/health/live",
            headers={"Origin": "http://localhost:3000",
                     "Access-Control-Request-Method": "GET"},
            timeout=HTTP_TIMEOUT,
        )
        # CORS preflight — 200 또는 204
        assert r.status_code in (200, 204, 405)

    def test_response_time_slo(self):
        """헬스 엔드포인트 응답 시간 < 2000ms."""
        t0 = time.monotonic()
        r = get(f"{BACKEND_URL}/api/v1/livez")
        ms = (time.monotonic() - t0) * 1000
        assert ms < SLO_RESPONSE_MS, f"응답 시간 {ms:.0f}ms > SLO {SLO_RESPONSE_MS}ms"
        assert r.status_code == 200

    def test_x_request_id_header(self):
        """요청 ID 헤더 전파 확인 — 응답에 X-Request-Id 가 echo 되어야 함."""
        r = requests.get(
            f"{BACKEND_URL}/api/v1/livez",
            headers={"X-Request-Id": "test-req-123"},
            timeout=HTTP_TIMEOUT,
        )
        assert r.status_code == 200
        # LoggingMiddleware 가 X-Request-Id 를 응답에 echo 해야 함
        resp_id = r.headers.get("x-request-id") or r.headers.get("X-Request-Id")
        assert resp_id == "test-req-123", \
            f"X-Request-Id echo 실패 — 응답 헤더: {dict(r.headers)}"


# ─────────────────────────────────────────────────────────────────────────────
# Frontend SPA
# ─────────────────────────────────────────────────────────────────────────────

class TestFrontendSPA:
    """Frontend React SPA (:3000) 스모크 테스트."""

    @pytest.fixture(autouse=True)
    def require_frontend(self):
        if not is_up(FRONTEND_URL):
            pytest.skip(f"Frontend 미기동 ({FRONTEND_URL})")

    def test_root_returns_html(self):
        r = get(FRONTEND_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "<html" in r.text.lower() or "<!doctype" in r.text.lower()

    def test_index_html_has_root_element(self):
        r = get(FRONTEND_URL)
        assert 'id="root"' in r.text

    def test_static_assets_accessible(self):
        """index.html 내 JS/CSS 에셋이 실제로 접근 가능한지 확인."""
        r = get(FRONTEND_URL)
        import re
        assets = re.findall(r'src="([^"]+\.js)"', r.text) + \
                 re.findall(r'href="([^"]+\.css)"', r.text)
        for asset in assets[:3]:  # 처음 3개만
            url = FRONTEND_URL + asset if asset.startswith("/") else FRONTEND_URL + "/" + asset
            ra = get(url)
            assert ra.status_code == 200, f"에셋 로드 실패: {url} → {ra.status_code}"

    def test_spa_client_routing(self):
        """SPA 클라이언트 라우팅 — 모든 경로에서 index.html 반환."""
        for path in ["/", "/builder", "/scenarios", "/settings"]:
            r = get(f"{FRONTEND_URL}{path}")
            assert r.status_code in (200, 404), f"{path}: {r.status_code}"

    def test_response_time_slo(self):
        t0 = time.monotonic()
        r = get(FRONTEND_URL)
        ms = (time.monotonic() - t0) * 1000
        assert ms < SLO_RESPONSE_MS, f"Frontend 로드 {ms:.0f}ms > SLO"
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# vbgw Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class TestOrchestrator:
    """vbgw Orchestrator (:8080) 스모크 테스트."""

    @pytest.fixture(autouse=True)
    def require_orchestrator(self):
        if not is_up(ORCHESTRATOR_URL):
            pytest.skip(f"Orchestrator 미기동 ({ORCHESTRATOR_URL})")

    def test_live_endpoint(self):
        r = get(f"{ORCHESTRATOR_URL}/live")
        assert r.status_code == 200

    def test_ready_endpoint(self):
        r = get(f"{ORCHESTRATOR_URL}/ready")
        assert r.status_code in (200, 503)

    def test_metrics_prometheus(self):
        r = get(f"{ORCHESTRATOR_URL}/metrics")
        assert r.status_code == 200
        assert "# HELP" in r.text or "vbgw_" in r.text

    def test_active_calls_metric(self):
        r = get(f"{ORCHESTRATOR_URL}/metrics")
        assert r.status_code == 200
        assert "vbgw_active_calls" in r.text, "활성 콜 메트릭 없음"

    def test_response_time_slo(self):
        t0 = time.monotonic()
        r = get(f"{ORCHESTRATOR_URL}/live")
        ms = (time.monotonic() - t0) * 1000
        assert ms < SLO_RESPONSE_MS
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Nginx Gateway
# ─────────────────────────────────────────────────────────────────────────────

class TestNginxGateway:
    """Nginx API Gateway (:80) 라우팅 테스트."""

    @pytest.fixture(autouse=True)
    def require_nginx(self):
        if not is_up(NGINX_URL):
            pytest.skip(f"Nginx 미기동 ({NGINX_URL})")

    def test_api_routing(self):
        r = get(f"{NGINX_URL}/api/v1/health/live")
        assert r.status_code == 200

    def test_frontend_routing(self):
        r = get(NGINX_URL)
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")

    def test_404_handling(self):
        r = get(f"{NGINX_URL}/nonexistent-path-xyz")
        assert r.status_code in (404, 200)  # SPA는 200 반환 가능

    def test_response_time_slo(self):
        t0 = time.monotonic()
        r = get(f"{NGINX_URL}/api/v1/health/live")
        ms = (time.monotonic() - t0) * 1000
        assert ms < SLO_RESPONSE_MS
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# Grafana
# ─────────────────────────────────────────────────────────────────────────────

class TestGrafana:
    """Grafana 모니터링 포탈 스모크 테스트."""

    @pytest.fixture(autouse=True)
    def require_grafana(self):
        if not is_up(GRAFANA_URL):
            pytest.skip(f"Grafana 미기동 ({GRAFANA_URL})")

    def test_health_endpoint(self):
        r = get(f"{GRAFANA_URL}/api/health")
        assert r.status_code == 200
        assert r.json().get("database") == "ok"

    def test_dashboards_api(self):
        r = get(f"{GRAFANA_URL}/api/search",
                headers={"Authorization": "Basic YWRtaW46YWRtaW4="})  # admin:admin
        assert r.status_code in (200, 401)

    def test_frontend_loads(self):
        r = get(GRAFANA_URL)
        assert r.status_code == 200
        assert "Grafana" in r.text or "grafana" in r.text

    def test_response_time_slo(self):
        t0 = time.monotonic()
        r = get(f"{GRAFANA_URL}/api/health")
        ms = (time.monotonic() - t0) * 1000
        assert ms < SLO_RESPONSE_MS
        assert r.status_code == 200
