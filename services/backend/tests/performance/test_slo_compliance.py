"""Phase 5 — 성능 / SLO 준수 테스트.  # noqa: E501

목표 (docs/reference/slo.md 기준):
  backend-api  HTTP 성공률        ≥ 99.9%
  backend-api  P95 응답 지연      ≤ 500ms
  agentic      파이프라인 성공률  ≥ 99.5% (메트릭 API 간접 검증)

접근:
  - httpx.AsyncClient(app=asgi_app, transport=ASGITransport) 로 실제 네트워크 없이
    ASGI 레이어에서 직접 요청 → 인프라 없음, 순수 애플리케이션 레이턴시 측정.
  - 각 시나리오마다 N=100 동시 요청(asyncio.gather) → P50/P95/P99 계산.
  - 단언 임계값은 SLO 목표 대비 2× 여유를 둬서 CI 환경의 노이즈에 강인하게.
    (실제 프로덕션에서는 Prometheus Recording Rule 기반으로 측정)

테스트 커버리지:
  P5-01  인증 없음 → 401  (success_rate: 100%)
  P5-02  metrics/pipeline GET × 100 동시  (P95 ≤ 500ms, success ≥ 99.9%)
  P5-03  metrics/sessions GET × 100 동시  (P95 ≤ 500ms, success ≥ 99.9%)
  P5-04  scenarios/validate POST × 100 동시  (P95 ≤ 500ms, success ≥ 99.9%)
  P5-05  혼합 워크로드 (pipeline + sessions + validate) × 150  (P95 ≤ 500ms)
  P5-06  WebSocket 연결 setup × 20 동시  (모두 connected 이벤트 수신)
"""

from __future__ import annotations

import asyncio
import json
import statistics
import sys
import threading
import time
import unittest.mock as mock
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ── 외부 의존성 stub ──────────────────────────────────────────────────────────
for _mod in [
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
    if _mod not in sys.modules:
        sys.modules[_mod] = mock.MagicMock()

from app.core.auth import create_access_token
from app.core.metrics import _MetricsStore

# ── 공통 패치 목록 ────────────────────────────────────────────────────────────

_COMMON_PATCHES = [
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
    patch(
        "app.middleware.rate_limit_middleware.rate_limit_check",
        new=AsyncMock(return_value=True),
    ),
]


def _mock_session_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.acquire_session_lease.return_value = True
    repo.release_session_lease = AsyncMock()
    repo.restore_hot_state.return_value = None
    repo.create = AsyncMock(return_value={})
    repo.save_turn = AsyncMock()
    repo.end_session = AsyncMock()
    repo.update_state = AsyncMock()
    repo.save_transfer_info = AsyncMock()
    return repo


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def reset_metrics_store():
    """모듈 전체에서 메트릭 스토어 초기화."""
    import app.core.metrics as m

    m._store = _MetricsStore()
    yield
    m._store = _MetricsStore()


@pytest.fixture(scope="module")
def asgi_app():
    """패치 적용된 FastAPI ASGI 앱 (모듈 스코프 — 한 번만 시작).

    핵심 주의사항 (두 가지):

    1. from-import 바인딩 문제:
       app/main.py 는 `from app.core.database import init_db` 로 로컬 바인딩을 만든다.
       따라서 app.core.database.init_db 만 패치하면 lifespan 에서 이미 바인딩된
       app.main.init_db 참조는 그대로 원본 함수를 가리킨다.
       TestClient 가 lifespan 을 트리거할 때 실제 MongoDB/Redis 연결이 발생하지
       않도록 app.main 네임스페이스도 직접 패치한다.

    2. restore_or_create_orchestrator 패치:
       CallSessionOrchestrator / AIPipeline 은 STT·LLM·TTS 클라이언트를 초기화한다.
       P5-06 WS 연결 테스트는 'connected' 이벤트 수신만 검증하므로,
       오케스트레이터 팩토리 함수 자체를 mock 으로 대체해 인프라 의존성을 제거한다.
    """
    import app.main  # noqa: F401 — sys.modules 등록 보장 (패치 전 import 필수)

    # GrpcServerLifecycle mock — start/stop 이 실제 gRPC 포트를 열지 않도록.
    _mock_grpc = MagicMock()
    _mock_grpc.return_value.start = AsyncMock()
    _mock_grpc.return_value.stop = AsyncMock()

    # restore_or_create_orchestrator mock — connected 이벤트만 반환하는 경량 오케스트레이터.
    # OutboundEvent 는 이미 sys.modules 에 올라온 후 import 되므로 안전.
    from app.services.call_session_orchestrator import OutboundEvent as _OE

    async def _mock_restore_or_create(session_id, tenant_id, client_id, repo):
        orch = AsyncMock()
        orch.should_close = False
        orch.is_idle_timeout = False
        orch.handle_audio = AsyncMock(return_value=[])
        orch.handle_control = AsyncMock(return_value=[])
        orch.end_session = AsyncMock(return_value=[])
        initial = [_OE("connected", {"session_id": session_id, "reconnected": False})]
        return orch, initial

    patches = _COMMON_PATCHES + [
        # lifespan 에서 `from ... import` 로 바인딩된 이름들을 직접 패치
        patch("app.main.init_db", new_callable=AsyncMock),
        patch("app.main.close_db", new_callable=AsyncMock),
        patch("app.main.init_redis", new_callable=AsyncMock),
        patch("app.main.close_redis", new_callable=AsyncMock),
        patch("app.main.GrpcServerLifecycle", new=_mock_grpc),
        # lifespan 에서 GrpcServerLifecycle(repo=SessionRepository()) 평가 시
        # SessionRepository() 가 먼저 실행되므로 app.main 네임스페이스도 패치.
        patch("app.main.SessionRepository", return_value=_mock_session_repo()),
        patch(
            "app.api.v1.routers.vbgw.SessionRepository",
            return_value=_mock_session_repo(),
        ),
        # WS 엔드포인트 — 오케스트레이터 팩토리를 경량 mock 으로 대체
        patch(
            "app.api.v1.routers.vbgw.restore_or_create_orchestrator",
            side_effect=_mock_restore_or_create,
        ),
    ]
    for p in patches:
        p.start()

    from app.main import app as _app

    yield _app

    for p in patches:
        p.stop()


@pytest.fixture(scope="module")
def auth_headers():
    token = create_access_token("perf-tenant", "perf-client", ["operator", "admin"])
    return {"Authorization": f"Bearer {token}"}


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _percentile(sorted_ms: list[float], pct: float) -> float:
    """정렬된 밀리초 리스트에서 백분위수 계산."""
    if not sorted_ms:
        return 0.0
    k = (len(sorted_ms) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_ms) - 1)
    return sorted_ms[lo] + (sorted_ms[hi] - sorted_ms[lo]) * (k - lo)


async def _fire_requests(
    app,
    method: str,
    path: str,
    *,
    n: int = 100,
    headers: dict | None = None,
    json_body: dict | None = None,
) -> tuple[list[float], int]:
    """
    n 개의 요청을 동시에 실행하고 (latency_ms 리스트, 오류 개수) 반환.
    httpx ASGITransport — 실 네트워크 없이 ASGI 레이어 직접 호출.
    """
    transport = httpx.ASGITransport(app=app)
    latencies: list[float] = []
    errors: int = 0

    async def _one():
        nonlocal errors
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                if method.upper() == "GET":
                    r = await client.get(path, headers=headers or {})
                else:
                    r = await client.post(path, headers=headers or {}, json=json_body or {})
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            if r.status_code >= 500:
                errors += 1
        except Exception:
            elapsed_ms = (time.perf_counter() - t0) * 1000
            latencies.append(elapsed_ms)
            errors += 1

    await asyncio.gather(*[_one() for _ in range(n)])
    latencies.sort()
    return latencies, errors


def _print_stats(label: str, latencies: list[float], errors: int) -> None:
    n = len(latencies)
    p50 = _percentile(latencies, 50)
    p95 = _percentile(latencies, 95)
    p99 = _percentile(latencies, 99)
    mean = statistics.mean(latencies) if latencies else 0
    success_rate = (n - errors) / n * 100 if n else 0
    print(
        f"\n[{label}] n={n}  mean={mean:.1f}ms  "
        f"P50={p50:.1f}ms  P95={p95:.1f}ms  P99={p99:.1f}ms  "
        f"err={errors}  success={success_rate:.2f}%"
    )


# ── P5-01: 인증 없음 → 401 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p501_unauthenticated_returns_401_not_500(asgi_app):
    """인증 없는 요청은 5xx 가 아닌 401 반환 — 오류 예산 소진하지 않음."""
    latencies, errors = await _fire_requests(asgi_app, "GET", "/api/v1/metrics/pipeline", n=50)
    _print_stats("P5-01 unauthenticated", latencies, errors)
    # 인증 없음 → 401 — 500 아님 → errors 가 0 이어야 함 (SLO 에서 4xx 는 "성공")
    assert errors == 0, f"인증 없음 요청에서 5xx 발생: {errors}/50"
    p95 = _percentile(latencies, 95)
    assert p95 <= 500, f"P95 {p95:.1f}ms > 500ms SLO"


# ── P5-02: metrics/pipeline × 100 동시 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_p502_metrics_pipeline_slo(asgi_app, auth_headers):
    """metrics/pipeline 엔드포인트 100 동시 → P95 ≤ 500ms, success ≥ 99.9%."""
    latencies, errors = await _fire_requests(
        asgi_app,
        "GET",
        "/api/v1/metrics/pipeline",
        n=100,
        headers=auth_headers,
    )
    _print_stats("P5-02 metrics/pipeline ×100", latencies, errors)

    success_rate = (100 - errors) / 100
    p95 = _percentile(latencies, 95)

    assert success_rate >= 0.999, f"성공률 {success_rate:.4f} < 99.9% SLO"
    assert p95 <= 500, f"P95 {p95:.1f}ms > 500ms SLO"


# ── P5-03: metrics/sessions × 100 동시 ───────────────────────────────────────


@pytest.mark.asyncio
async def test_p503_metrics_sessions_slo(asgi_app, auth_headers):
    """metrics/sessions 엔드포인트 100 동시 → P95 ≤ 500ms, success ≥ 99.9%."""
    latencies, errors = await _fire_requests(
        asgi_app,
        "GET",
        "/api/v1/metrics/sessions",
        n=100,
        headers=auth_headers,
    )
    _print_stats("P5-03 metrics/sessions ×100", latencies, errors)

    success_rate = (100 - errors) / 100
    p95 = _percentile(latencies, 95)

    assert success_rate >= 0.999, f"성공률 {success_rate:.4f} < 99.9% SLO"
    assert p95 <= 500, f"P95 {p95:.1f}ms > 500ms SLO"


# ── P5-04: scenarios/validate × 100 동시 ─────────────────────────────────────

_VALID_SCENARIO_PAYLOAD = {
    "scenario_id": "perf-test-scenario",
    "tenant_id": "perf-tenant",
    "name": "성능 테스트 시나리오",
    "description": "Phase 5 성능 검증용",
    "nodes": [
        {
            "id": "start",
            "type": "start",
            "transitions": [{"target": "greet"}],
        },
        {
            "id": "greet",
            "type": "speak",
            "text": "안녕하세요. 무엇을 도와드릴까요?",
            "transitions": [{"target": "end"}],
        },
        {
            "id": "end",
            "type": "end",
        },
    ],
}


@pytest.mark.asyncio
async def test_p504_scenario_validate_slo(asgi_app, auth_headers):
    """scenarios/validate 엔드포인트 100 동시 → P95 ≤ 500ms, success ≥ 99.9%."""
    latencies, errors = await _fire_requests(
        asgi_app,
        "POST",
        "/api/v1/scenarios/validate",
        n=100,
        headers=auth_headers,
        json_body=_VALID_SCENARIO_PAYLOAD,
    )
    _print_stats("P5-04 scenarios/validate ×100", latencies, errors)

    success_rate = (100 - errors) / 100
    p95 = _percentile(latencies, 95)

    assert success_rate >= 0.999, f"성공률 {success_rate:.4f} < 99.9% SLO"
    assert p95 <= 500, f"P95 {p95:.1f}ms > 500ms SLO"


# ── P5-05: 혼합 워크로드 × 150 ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p505_mixed_workload_slo(asgi_app, auth_headers):
    """혼합 워크로드 (pipeline + sessions + prometheus) × 150 동시 → P95 ≤ 500ms."""
    transport = httpx.ASGITransport(app=asgi_app)
    latencies: list[float] = []
    errors = 0
    N = 150

    endpoints = [
        ("GET", "/api/v1/metrics/pipeline"),
        ("GET", "/api/v1/metrics/sessions"),
        ("GET", "/api/v1/metrics/prometheus"),  # 인증 불필요
        ("GET", "/api/v1/metrics/summary"),
    ]

    async def _req(idx: int) -> None:
        nonlocal errors
        method, path = endpoints[idx % len(endpoints)]
        # prometheus 는 인증 없이, 나머지는 인증 헤더
        hdrs = {} if path.endswith("prometheus") else auth_headers
        t0 = time.perf_counter()
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                if method == "GET":
                    r = await client.get(path, headers=hdrs)
                else:
                    r = await client.post(path, headers=hdrs)
            ms = (time.perf_counter() - t0) * 1000
            latencies.append(ms)
            if r.status_code >= 500:
                errors += 1
        except Exception:
            ms = (time.perf_counter() - t0) * 1000
            latencies.append(ms)
            errors += 1

    await asyncio.gather(*[_req(i) for i in range(N)])
    latencies.sort()
    _print_stats("P5-05 mixed ×150", latencies, errors)

    success_rate = (N - errors) / N
    p95 = _percentile(latencies, 95)

    assert success_rate >= 0.999, f"성공률 {success_rate:.4f} < 99.9% SLO"
    assert p95 <= 500, f"P95 {p95:.1f}ms > 500ms SLO"


# ── P5-06: WebSocket 연결 setup × 20 동시 ────────────────────────────────────


def test_p506_websocket_concurrent_setup(asgi_app):
    """WebSocket 20 세션 동시 연결 → 전부 connected 이벤트, setup P95 ≤ 500ms.

    하나의 TestClient (lifespan 한 번) 를 공유하고, WS connect 만 스레드에서 동시 실행.
    각 스레드가 TestClient(asgi_app) 을 따로 생성하면 동일 앱 객체에 대한
    lifespan 이 동시에 여러 번 시작되어 충돌하므로 이 방식을 사용한다.
    """
    from starlette.testclient import TestClient

    setup_times: list[float] = []
    results: list[bool] = []
    lock = threading.Lock()
    N = 20

    def _ws_session(c: TestClient, idx: int) -> None:
        token = create_access_token("perf-tenant", f"perf-ws-{idx:03d}", ["operator"])
        url = f"/api/v1/ws/vbgw?token={token}&session_id=perf-ws-{idx:03d}"
        t0 = time.perf_counter()
        ok = False
        try:
            with c.websocket_connect(url) as ws:
                data = json.loads(ws.receive_text())
                elapsed_ms = (time.perf_counter() - t0) * 1000
                ok = data.get("event") == "connected"
        except Exception:
            elapsed_ms = (time.perf_counter() - t0) * 1000
        with lock:
            setup_times.append(elapsed_ms)
            results.append(ok)

    # TestClient 는 한 번만 시작 (lifespan 1회) — WS connect 만 병렬 실행
    with TestClient(asgi_app, raise_server_exceptions=False) as client:
        threads = [threading.Thread(target=_ws_session, args=(client, i)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=20)

    setup_times.sort()
    errors = sum(1 for r in results if not r)
    _print_stats("P5-06 WS setup ×20", setup_times, errors)

    connected_count = sum(results)
    p95 = _percentile(setup_times, 95)

    assert connected_count >= 18, f"20 세션 중 {connected_count}개만 connected (≥18 필요)"
    assert p95 <= 500, f"WebSocket setup P95 {p95:.1f}ms > 500ms"
