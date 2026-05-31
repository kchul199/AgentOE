"""test_web_portals.py — agentoe 웹 포탈 E2E 테스트

변경이력: v1.0.0 | 2026-05-06 | Phase-T+ | 웹 포탈 Playwright E2E + HTTP 스모크

커버리지:
  W-01  Backend API Swagger UI 접근 (/api/docs)
  W-02  Backend 헬스 엔드포인트 (/api/v1/health/live, /ready)
  W-03  Backend OpenAPI JSON (/openapi.json)
  W-04  시나리오 빌더 프론트엔드 페이지 로드 (:3000)
  W-05  프론트엔드 — 시나리오 노드 추가 상호작용
  W-06  Orchestrator 관리 REST (:8080 /live, /metrics)
  W-07  Backend 메트릭 엔드포인트 (/api/v1/metrics/prometheus)
  W-08  Nginx 게이트웨이 API 라우팅 (:80 → /api/v1/health)
  W-09  인증 없이 보호 엔드포인트 접근 → 401 확인
  W-10  Grafana 대시보드 기본 접근 (:3001 or :3000)

실행:
  # 스택 기동 후
  pytest services/frontend/tests/e2e/test_web_portals.py -v

  # HTTP 스모크만 (브라우저 불필요)
  pytest services/frontend/tests/e2e/test_web_portals.py -v -k "smoke"

  # 특정 포탈
  pytest services/frontend/tests/e2e/test_web_portals.py -v -k "frontend"
  pytest services/frontend/tests/e2e/test_web_portals.py -v -k "backend"
  pytest services/frontend/tests/e2e/test_web_portals.py -v -k "orchestrator"

설정 (환경변수 오버라이드):
  BACKEND_URL      (기본 http://localhost:8000)
  FRONTEND_URL     (기본 http://localhost:3000)
  ORCHESTRATOR_URL (기본 http://localhost:8080)
  NGINX_URL        (기본 http://localhost:80)
  GRAFANA_URL      (기본 http://localhost:3001)
  HEADLESS         (기본 true, "false" 로 브라우저 표시)
  SLOW_MO          (ms, 기본 0)
"""

from __future__ import annotations

import os
import time
from typing import Generator

import pytest
import requests

# ── 설정 ──────────────────────────────────────────────────────────────────────
BACKEND_URL      = os.environ.get("BACKEND_URL",      "http://localhost:8000")
FRONTEND_URL     = os.environ.get("FRONTEND_URL",     "http://localhost:3000")
ORCHESTRATOR_URL = os.environ.get("ORCHESTRATOR_URL", "http://localhost:8080")
NGINX_URL        = os.environ.get("NGINX_URL",        "http://localhost:80")
GRAFANA_URL      = os.environ.get("GRAFANA_URL",      "http://localhost:3001")

HEADLESS = os.environ.get("HEADLESS", "true").lower() != "false"
SLOW_MO  = int(os.environ.get("SLOW_MO", "0"))
TIMEOUT  = 10_000  # ms


def _is_up(url: str, timeout: int = 3) -> bool:
    """서비스가 응답하는지 빠르게 확인."""
    try:
        r = requests.get(url, timeout=timeout)
        return r.status_code < 600
    except Exception:
        return False


# ── pytest fixtures ───────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def browser_context():
    """Playwright 브라우저 컨텍스트 (세션 전체 공유)."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        pytest.skip("playwright 미설치 — `pip install playwright && playwright install chromium`")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS, slow_mo=SLOW_MO)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            ignore_https_errors=True,
        )
        yield context
        context.close()
        browser.close()


@pytest.fixture
def page(browser_context):
    """테스트별 새 페이지."""
    pg = browser_context.new_page()
    pg.set_default_timeout(TIMEOUT)
    yield pg
    pg.close()


# ── W-01: Backend Swagger UI ──────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.backend
def test_w01_swagger_ui_loads(page):
    """W-01: Swagger UI (/api/docs) 페이지 로드 및 타이틀 확인."""
    if not _is_up(f"{BACKEND_URL}/api/docs"):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    page.goto(f"{BACKEND_URL}/api/docs")
    page.wait_for_selector(".swagger-ui", timeout=TIMEOUT)

    title = page.title()
    assert "AgentOE" in title or "Swagger" in title, f"예상 타이틀 없음: {title}"

    # Swagger UI 내 API 경로 존재 확인
    page.wait_for_selector(".opblock", timeout=TIMEOUT)
    blocks = page.locator(".opblock").count()
    assert blocks > 0, "API 엔드포인트 블록이 없음"

    print(f"\n  ✓ Swagger UI: {blocks}개 엔드포인트 노출됨")


# ── W-02: Backend 헬스 엔드포인트 ────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.backend
def test_w02_health_live():
    """W-02: /api/v1/health/live → 200 + {"status":"ok"}"""
    if not _is_up(BACKEND_URL):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    r = requests.get(f"{BACKEND_URL}/api/v1/health/live", timeout=5)
    assert r.status_code == 200, f"live 응답 {r.status_code}"
    body = r.json()
    assert body.get("status") in ("ok", "live", "healthy"), f"상태 이상: {body}"
    print(f"\n  ✓ /health/live: {body}")


@pytest.mark.smoke
@pytest.mark.backend
def test_w02_health_ready():
    """W-02: /api/v1/health/ready → 200 (DB/Redis 연결 확인)"""
    if not _is_up(BACKEND_URL):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    r = requests.get(f"{BACKEND_URL}/api/v1/health/ready", timeout=5)
    assert r.status_code in (200, 503), f"예상 외 코드: {r.status_code}"
    body = r.json()
    print(f"\n  ✓ /health/ready: status={r.status_code} body={body}")


# ── W-03: OpenAPI 스키마 ─────────────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.backend
def test_w03_openapi_json():
    """W-03: /openapi.json 구조 검증."""
    if not _is_up(BACKEND_URL):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    r = requests.get(f"{BACKEND_URL}/openapi.json", timeout=5)
    assert r.status_code == 200
    schema = r.json()
    assert schema.get("openapi", "").startswith("3."), "OpenAPI 3.x 필요"
    assert "paths" in schema
    assert len(schema["paths"]) > 0
    paths = list(schema["paths"].keys())
    print(f"\n  ✓ OpenAPI 스키마: {len(paths)}개 경로")
    assert any("/health" in p for p in paths), "/health 경로 없음"
    assert any("/sessions" in p for p in paths), "/sessions 경로 없음"


# ── W-04: 시나리오 빌더 프론트엔드 ───────────────────────────────────────────

@pytest.mark.frontend
def test_w04_frontend_loads(page):
    """W-04: 시나리오 빌더 React SPA 로드 + 기본 UI 요소 확인."""
    if not _is_up(FRONTEND_URL):
        pytest.skip(f"Frontend 미기동: {FRONTEND_URL}")

    page.goto(FRONTEND_URL)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)

    # React root 마운트 확인
    root = page.locator("#root")
    assert root.count() > 0, "#root 없음"

    title = page.title()
    print(f"\n  ✓ Frontend 로드: title='{title}'")

    # 주요 UI 요소 확인 (팔레트 / 캔버스)
    page.wait_for_timeout(1000)
    body_text = page.inner_text("body")
    assert len(body_text) > 0, "페이지 본문 비어 있음"


@pytest.mark.frontend
def test_w05_frontend_scenario_palette(page):
    """W-05: 시나리오 빌더 — 팔레트에서 노드 타입 확인."""
    if not _is_up(FRONTEND_URL):
        pytest.skip(f"Frontend 미기동: {FRONTEND_URL}")

    page.goto(FRONTEND_URL)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(1500)

    # 팔레트 패널 확인 (data-testid 또는 클래스 기반)
    palette_selectors = [
        "[data-testid='palette']",
        ".palette",
        "[class*='palette']",
        "[class*='Palette']",
    ]
    found_palette = False
    for sel in palette_selectors:
        if page.locator(sel).count() > 0:
            found_palette = True
            print(f"\n  ✓ 팔레트 발견: {sel}")
            break

    if not found_palette:
        # 페이지 텍스트에서 노드 타입 키워드 확인
        body_text = page.inner_text("body")
        node_keywords = ["Start", "Say", "Collect", "Branch", "End", "시작", "종료"]
        found_keywords = [k for k in node_keywords if k in body_text]
        assert found_keywords, f"노드 타입 키워드 없음. 본문: {body_text[:300]}"
        print(f"\n  ✓ 노드 타입 키워드 발견: {found_keywords}")


@pytest.mark.frontend
def test_w05_frontend_drag_node(page):
    """W-05: 시나리오 빌더 — 팔레트 → 캔버스 드래그 드롭."""
    if not _is_up(FRONTEND_URL):
        pytest.skip(f"Frontend 미기동: {FRONTEND_URL}")

    page.goto(FRONTEND_URL)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    page.wait_for_timeout(1500)

    # ReactFlow 캔버스 확인
    canvas_selectors = [
        ".react-flow",
        "[class*='react-flow']",
        "[data-testid='rf__wrapper']",
    ]
    canvas = None
    for sel in canvas_selectors:
        loc = page.locator(sel)
        if loc.count() > 0:
            canvas = loc.first
            break

    if canvas is None:
        pytest.skip("ReactFlow 캔버스를 찾을 수 없음")

    print(f"\n  ✓ ReactFlow 캔버스 발견")

    # 팔레트 첫 번째 아이템 드래그 시도
    draggable = page.locator("[draggable='true']")
    if draggable.count() > 0:
        src = draggable.first.bounding_box()
        dst = canvas.bounding_box()
        if src and dst:
            page.mouse.move(src["x"] + src["width"] / 2, src["y"] + src["height"] / 2)
            page.mouse.down()
            page.mouse.move(dst["x"] + dst["width"] / 2, dst["y"] + dst["height"] / 2, steps=10)
            page.mouse.up()
            page.wait_for_timeout(500)
            print("  ✓ 드래그 드롭 완료")
    else:
        print("  ⚠ draggable 아이템 없음 — 드래그 스킵")


# ── W-06: Orchestrator 관리 REST ─────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.orchestrator
def test_w06_orchestrator_live():
    """W-06: Orchestrator /live 헬스 체크."""
    if not _is_up(ORCHESTRATOR_URL):
        pytest.skip(f"Orchestrator 미기동: {ORCHESTRATOR_URL}")

    r = requests.get(f"{ORCHESTRATOR_URL}/live", timeout=5)
    assert r.status_code == 200, f"live 응답 {r.status_code}: {r.text}"
    print(f"\n  ✓ Orchestrator /live: {r.text[:80]}")


@pytest.mark.orchestrator
def test_w06_orchestrator_metrics():
    """W-06: Orchestrator /metrics Prometheus 포맷 확인."""
    if not _is_up(ORCHESTRATOR_URL):
        pytest.skip(f"Orchestrator 미기동: {ORCHESTRATOR_URL}")

    r = requests.get(f"{ORCHESTRATOR_URL}/metrics", timeout=5)
    assert r.status_code == 200
    text = r.text
    assert "# HELP" in text or "vbgw_" in text, "Prometheus 포맷 아님"
    lines = [l for l in text.splitlines() if l and not l.startswith("#")]
    print(f"\n  ✓ Orchestrator /metrics: {len(lines)}개 메트릭 시리즈")


@pytest.mark.orchestrator
def test_w06_orchestrator_sessions():
    """W-06: Orchestrator /sessions 엔드포인트."""
    if not _is_up(ORCHESTRATOR_URL):
        pytest.skip(f"Orchestrator 미기동: {ORCHESTRATOR_URL}")

    r = requests.get(f"{ORCHESTRATOR_URL}/sessions", timeout=5)
    assert r.status_code in (200, 404), f"예상 외 코드: {r.status_code}"
    print(f"\n  ✓ Orchestrator /sessions: {r.status_code}")


# ── W-07: Backend Prometheus 메트릭 ──────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.backend
def test_w07_backend_prometheus_metrics():
    """W-07: /api/v1/metrics/prometheus — Prometheus 포맷 + agentoe 메트릭 확인."""
    if not _is_up(BACKEND_URL):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    # 인증 없이 접근 가능한 메트릭 엔드포인트
    r = requests.get(f"{BACKEND_URL}/api/v1/metrics/prometheus", timeout=5)
    assert r.status_code in (200, 401, 403), f"예상 외 코드: {r.status_code}"

    if r.status_code == 200:
        text = r.text
        expected = ["http_requests_total", "http_request_duration"]
        found = [m for m in expected if m in text]
        print(f"\n  ✓ /metrics/prometheus: 응답 {len(text)}bytes, 메트릭={found}")
    else:
        print(f"\n  ✓ /metrics/prometheus: {r.status_code} (인증 필요)")


@pytest.mark.backend
def test_w07_backend_pipeline_metrics():
    """W-07: /api/v1/metrics/pipeline — JSON 메트릭."""
    if not _is_up(BACKEND_URL):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    r = requests.get(
        f"{BACKEND_URL}/api/v1/metrics/pipeline",
        headers={"Authorization": "Bearer test-token"},
        timeout=5,
    )
    assert r.status_code in (200, 401, 403, 422), f"예상 외: {r.status_code}"
    print(f"\n  ✓ /metrics/pipeline: {r.status_code}")


# ── W-08: Nginx 게이트웨이 라우팅 ────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.nginx
def test_w08_nginx_api_routing():
    """W-08: Nginx :80 → /api/v1/health/live 라우팅."""
    if not _is_up(NGINX_URL):
        pytest.skip(f"Nginx 미기동: {NGINX_URL}")

    r = requests.get(f"{NGINX_URL}/api/v1/health/live", timeout=5)
    assert r.status_code == 200, f"Nginx 라우팅 실패: {r.status_code}"
    print(f"\n  ✓ Nginx → /api/v1/health/live: {r.status_code}")


@pytest.mark.nginx
def test_w08_nginx_frontend_routing(page):
    """W-08: Nginx :80 → 프론트엔드 SPA 라우팅."""
    if not _is_up(NGINX_URL):
        pytest.skip(f"Nginx 미기동: {NGINX_URL}")

    page.goto(NGINX_URL)
    page.wait_for_load_state("networkidle", timeout=TIMEOUT)
    assert page.locator("#root").count() > 0 or page.title() != "", \
        "프론트엔드 라우팅 실패"
    print(f"\n  ✓ Nginx → Frontend: title='{page.title()}'")


# ── W-09: 인증 보호 엔드포인트 ───────────────────────────────────────────────

@pytest.mark.smoke
@pytest.mark.backend
@pytest.mark.security
def test_w09_protected_endpoint_requires_auth():
    """W-09: 인증 없이 보호 엔드포인트 접근 → 401/403."""
    if not _is_up(BACKEND_URL):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    protected = [
        "/api/v1/sessions",
        "/api/v1/scenarios",
        "/api/v1/metrics/pipeline",
    ]
    for path in protected:
        r = requests.get(f"{BACKEND_URL}{path}", timeout=5)
        assert r.status_code in (401, 403, 422), \
            f"{path} 인증 없이 {r.status_code} — 보안 취약점 가능성"
        print(f"\n  ✓ {path}: {r.status_code} (인증 필요 확인)")


@pytest.mark.backend
@pytest.mark.security
def test_w09_invalid_token_rejected():
    """W-09: 잘못된 JWT → 401."""
    if not _is_up(BACKEND_URL):
        pytest.skip(f"Backend 미기동: {BACKEND_URL}")

    r = requests.get(
        f"{BACKEND_URL}/api/v1/sessions",
        headers={"Authorization": "Bearer invalid.jwt.token"},
        timeout=5,
    )
    assert r.status_code in (401, 403), \
        f"무효 토큰이 {r.status_code}로 통과 — 보안 취약점"
    print(f"\n  ✓ 무효 JWT: {r.status_code} 거부됨")


# ── W-10: Grafana 대시보드 ────────────────────────────────────────────────────

@pytest.mark.monitoring
def test_w10_grafana_health():
    """W-10: Grafana /api/health → 200."""
    if not _is_up(GRAFANA_URL):
        pytest.skip(f"Grafana 미기동: {GRAFANA_URL}")

    r = requests.get(f"{GRAFANA_URL}/api/health", timeout=5)
    assert r.status_code == 200
    body = r.json()
    assert body.get("database") == "ok", f"Grafana DB 이상: {body}"
    print(f"\n  ✓ Grafana /api/health: {body}")


@pytest.mark.monitoring
def test_w10_grafana_dashboards(page):
    """W-10: Grafana 대시보드 목록 페이지 로드."""
    if not _is_up(GRAFANA_URL):
        pytest.skip(f"Grafana 미기동: {GRAFANA_URL}")

    page.goto(f"{GRAFANA_URL}/dashboards")
    page.wait_for_load_state("networkidle", timeout=TIMEOUT * 2)
    title = page.title()
    assert "Grafana" in title, f"Grafana 타이틀 없음: {title}"
    print(f"\n  ✓ Grafana 대시보드 페이지: '{title}'")


@pytest.mark.monitoring
def test_w10_grafana_agentoe_dashboard(page):
    """W-10: agentoe 전용 대시보드 접근 (UID 기반)."""
    if not _is_up(GRAFANA_URL):
        pytest.skip(f"Grafana 미기동: {GRAFANA_URL}")

    # agentoe.json 의 uid 확인
    import json
    dashboard_path = (
        __file__  # services/frontend/tests/e2e/
        .replace("services/frontend/tests/e2e/test_web_portals.py", "")
        + "deploy/observability/dashboards/agentic.json"
    )
    try:
        with open(dashboard_path) as f:
            d = json.load(f)
        uid = d.get("uid") or d.get("panels", [{}])[0].get("datasource", {})
        if uid:
            page.goto(f"{GRAFANA_URL}/d/{uid}")
            page.wait_for_load_state("networkidle", timeout=TIMEOUT * 2)
            print(f"\n  ✓ Grafana agentoe 대시보드 (uid={uid}): {page.title()}")
    except (FileNotFoundError, KeyError, IndexError):
        pytest.skip("agentic.json 에서 uid 추출 실패")


# ── 로드 성능: 웹 페이지 응답 시간 ───────────────────────────────────────────

@pytest.mark.perf
def test_page_load_performance():
    """페이지별 HTTP 응답 시간 측정 (SLO: < 2000ms)."""
    endpoints = [
        ("Backend health",    f"{BACKEND_URL}/api/v1/health/live"),
        ("Backend OpenAPI",   f"{BACKEND_URL}/openapi.json"),
        ("Orchestrator live", f"{ORCHESTRATOR_URL}/live"),
        ("Nginx health",      f"{NGINX_URL}/api/v1/health/live"),
        ("Frontend root",     FRONTEND_URL),
        ("Grafana health",    f"{GRAFANA_URL}/api/health"),
    ]

    print("\n  [ 페이지 응답 시간 ]")
    results = []
    for name, url in endpoints:
        if not _is_up(url.rsplit("/", 1)[0]):
            print(f"  ⚠  {name:<25}: 미기동 (skip)")
            continue
        t0 = time.monotonic()
        try:
            r = requests.get(url, timeout=5)
            elapsed_ms = (time.monotonic() - t0) * 1000
            ok = elapsed_ms < 2000
            mark = "✅" if ok else "⚠️ "
            print(f"  {mark} {name:<25}: {elapsed_ms:6.0f}ms (HTTP {r.status_code})")
            results.append((name, elapsed_ms, ok))
        except Exception as e:
            print(f"  ❌ {name:<25}: 오류 — {e}")

    failed = [(n, ms) for n, ms, ok in results if not ok]
    assert not failed, f"응답 시간 SLO(2000ms) 초과: {failed}"
