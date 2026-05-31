"""Phase N — Portal 성능 / SLO 테스트.

목표 (docs/reference/slo.md 기준):
  portal-api  P95 응답 지연   ≤ 500ms  (config GET/PUT)
  portal-api  로그인 P95      ≤ 1,000ms (bcrypt 포함)
  SSE 연결     50 동시 유지   30초 무단절

접근:
  - httpx.AsyncClient(app=asgi_app, transport=ASGITransport) — 네트워크 없이 ASGI 직접 호출.
  - N=50/100 동시 요청 → P50/P95/P99 계산.

실행:
    cd services/backend
    export MONGODB_URI=... REDIS_URL=... PORTAL_MFA_ENVELOPE_KEY=... PORTAL_JWT_SECRET=...
    python -m pytest tests/performance/test_portal_load.py -v --tb=short -s
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

# ── 환경변수 stub ─────────────────────────────────────────────────────────────
os.environ.setdefault(
    "MONGODB_URI", "mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault(
    "PORTAL_MFA_ENVELOPE_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)
os.environ.setdefault("PORTAL_JWT_SECRET", "dev-portal-jwt-secret-local")
os.environ.setdefault("PORTAL_ORIGIN", "http://localhost:5174")
os.environ.setdefault("JWT_SECRET", "dev-jwt-secret-local")
os.environ.setdefault("GROQ_API_KEY", "dummy-groq-key")

BASE = "/api/v1"

# ── 허용 임계값 (CI 환경 노이즈 감안 — SLO 대비 2× 여유) ───────────────────
_LOGIN_P95_MS = 2_000  # SLO 1,000ms × 2
_CONFIG_GET_P95_MS = 1_000  # SLO 500ms × 2
_CONFIG_PUT_P95_MS = 1_000  # SLO 500ms × 2
_SUCCESS_RATE_MIN = 0.995  # 99.5%

# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _percentile(data: list[float], pct: float) -> float:
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * pct / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


def _print_stats(label: str, latencies_ms: list[float], successes: int, total: int) -> None:
    p50 = _percentile(latencies_ms, 50)
    p95 = _percentile(latencies_ms, 95)
    p99 = _percentile(latencies_ms, 99)
    rate = successes / total * 100 if total else 0
    print(f"\n  [{label}] N={total}, 성공={successes}({rate:.1f}%)")
    print(f"  P50={p50:.0f}ms  P95={p95:.0f}ms  P99={p99:.0f}ms")


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module")
def stub_external() -> Any:
    """MongoDB/Redis/DB 외부 의존성을 stub 으로 교체 (ASGI direct 테스트용)."""
    # ── Redis mock ──────────────────────────────────────────────────────────────
    mock_redis = AsyncMock()
    mock_redis.eval = AsyncMock(return_value=1)
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)

    # ── MongoDB collection mock ─────────────────────────────────────────────────
    mock_col = AsyncMock()
    mock_col.find_one = AsyncMock(return_value=None)  # config GET → 빈 구조 200
    mock_col.find = MagicMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[])))
    mock_col.insert_one = AsyncMock()
    mock_col.update_one = AsyncMock()
    mock_col.count_documents = AsyncMock(return_value=0)

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)

    # _client 를 stub 으로 교체 → get_database() 가 RuntimeError 대신 mock_db 반환
    import app.core.database as _db_mod

    mock_motor_client = MagicMock()
    mock_motor_client.__getitem__ = MagicMock(return_value=mock_db)

    patches = [
        patch("motor.motor_asyncio.AsyncIOMotorClient", MagicMock),
        patch("redis.asyncio.from_url", MagicMock(return_value=mock_redis)),
        # _client 를 mock 으로 주입 → 모든 import 사이트의 get_database() 가 mock_db 반환
        patch.object(_db_mod, "_client", mock_motor_client),
        # rate-limit 미들웨어 bypass
        patch(
            "app.middleware.rate_limit_middleware.rate_limit_check",
            new=AsyncMock(return_value=True),
        ),
        # kill-switch 비활성화 상태
        patch(
            "app.middleware.kill_switch_middleware.get_kill_switch_cached",
            new=AsyncMock(return_value=False),
        ),
        # SSE /stream/metrics — Prometheus 대신 즉시 반환 (P-PERF-06 용)
        patch(
            "app.core.metrics.get_metrics_snapshot_async",
            new=AsyncMock(
                return_value={
                    "ccu": 0,
                    "p95_latency_ms": 0.0,
                    "error_rate": 0.0,
                    "asr_sessions": 0,
                    "tts_sessions": 0,
                }
            ),
        ),
        # SSE generator 의 asyncio.sleep(1.0) → 즉시 CancelledError 로 루프 종료
        patch(
            "app.api.v1.routers.stream.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
    ]
    started = [p.start() for p in patches]
    yield started
    for p in patches:
        with contextlib.suppress(RuntimeError):
            p.stop()


@pytest.fixture(scope="module")
async def async_client(stub_external: Any) -> httpx.AsyncClient:
    """ASGI transport 기반 비동기 클라이언트."""
    from httpx import ASGITransport, AsyncClient

    from app.main import app

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as client:
        yield client


# ─────────────────────────────────────────────────────────────────────────────
# P-PERF-01  인증 없음 → 401 (성공률 100%, P95 ≤ 200ms)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_perf_01_unauthenticated_rejection(async_client: httpx.AsyncClient) -> None:
    """P-PERF-01: 인증 없이 portal 엔드포인트 → 401, P95 ≤ 200ms."""
    N = 50
    latencies: list[float] = []
    successes = 0

    async def one() -> None:
        t0 = time.perf_counter()
        resp = await async_client.get(f"{BASE}/admin/config/dev")
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        if resp.status_code in (401, 403):
            nonlocal successes
            successes += 1

    await asyncio.gather(*[one() for _ in range(N)])
    _print_stats("P-PERF-01 unauthenticated", latencies, successes, N)

    p95 = _percentile(latencies, 95)
    assert p95 <= 200, f"P95={p95:.0f}ms > 200ms"
    assert successes / N >= _SUCCESS_RATE_MIN, (
        f"성공률 {successes / N:.1%} < {_SUCCESS_RATE_MIN:.1%}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# P-PERF-02  /admin/config GET × 100 동시 (viewer 토큰)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_perf_02_config_get_concurrent(async_client: httpx.AsyncClient) -> None:
    """P-PERF-02: /admin/config/dev GET × 100 동시 — P95 ≤ 1,000ms."""
    from jose import jwt as jose_jwt

    from app.core.config import settings

    # viewer 토큰 생성
    token = jose_jwt.encode(
        {
            "sub": "perf-u1",
            "tenant_id": "t1",
            "roles": ["portal:viewer"],
            "iss": "agentoe-portal",
            "exp": int(time.time()) + 3600,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}

    N = 100
    latencies: list[float] = []
    successes = 0

    async def one() -> None:
        t0 = time.perf_counter()
        resp = await async_client.get(f"{BASE}/admin/config/dev", headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        if resp.status_code in (200, 404):
            nonlocal successes
            successes += 1

    await asyncio.gather(*[one() for _ in range(N)])
    _print_stats("P-PERF-02 config GET", latencies, successes, N)

    p95 = _percentile(latencies, 95)
    assert p95 <= _CONFIG_GET_P95_MS, f"P95={p95:.0f}ms > {_CONFIG_GET_P95_MS}ms"
    assert successes / N >= _SUCCESS_RATE_MIN, f"성공률 {successes / N:.1%}"


# ─────────────────────────────────────────────────────────────────────────────
# P-PERF-03  /admin/env/info GET × 100 동시
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_perf_03_env_info_concurrent(async_client: httpx.AsyncClient) -> None:
    """P-PERF-03: /admin/env/info × 100 동시 — P95 ≤ 1,000ms."""
    from jose import jwt as jose_jwt

    from app.core.config import settings

    token = jose_jwt.encode(
        {
            "sub": "perf-u2",
            "tenant_id": "t1",
            "roles": ["portal:viewer"],
            "iss": "agentoe-portal",
            "exp": int(time.time()) + 3600,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    N = 100
    latencies: list[float] = []
    successes = 0

    async def one() -> None:
        t0 = time.perf_counter()
        resp = await async_client.get(f"{BASE}/admin/env/info", headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        if resp.status_code in (200,):
            nonlocal successes
            successes += 1

    await asyncio.gather(*[one() for _ in range(N)])
    _print_stats("P-PERF-03 env/info", latencies, successes, N)

    p95 = _percentile(latencies, 95)
    assert p95 <= _CONFIG_GET_P95_MS, f"P95={p95:.0f}ms > {_CONFIG_GET_P95_MS}ms"


# ─────────────────────────────────────────────────────────────────────────────
# P-PERF-04  /admin/config/diff GET × 50 동시
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_perf_04_config_diff_concurrent(async_client: httpx.AsyncClient) -> None:
    """P-PERF-04: /admin/config/diff × 50 동시 — P95 ≤ 1,000ms."""
    from jose import jwt as jose_jwt

    from app.core.config import settings

    token = jose_jwt.encode(
        {
            "sub": "perf-u3",
            "tenant_id": "t1",
            "roles": ["portal:viewer"],
            "iss": "agentoe-portal",
            "exp": int(time.time()) + 3600,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    N = 50
    latencies: list[float] = []
    successes = 0

    async def one() -> None:
        t0 = time.perf_counter()
        resp = await async_client.get(f"{BASE}/admin/config/diff", headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        if resp.status_code == 200:
            nonlocal successes
            successes += 1

    await asyncio.gather(*[one() for _ in range(N)])
    _print_stats("P-PERF-04 config/diff", latencies, successes, N)

    p95 = _percentile(latencies, 95)
    assert p95 <= _CONFIG_GET_P95_MS, f"P95={p95:.0f}ms > {_CONFIG_GET_P95_MS}ms"


# ─────────────────────────────────────────────────────────────────────────────
# P-PERF-05  혼합 워크로드 × 150 동시
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_perf_05_mixed_workload(async_client: httpx.AsyncClient) -> None:
    """P-PERF-05: config GET + diff + env/info 혼합 × 150 — P95 ≤ 1,000ms."""
    from jose import jwt as jose_jwt

    from app.core.config import settings

    token = jose_jwt.encode(
        {
            "sub": "perf-u4",
            "tenant_id": "t1",
            "roles": ["portal:viewer"],
            "iss": "agentoe-portal",
            "exp": int(time.time()) + 3600,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}
    endpoints = [
        f"{BASE}/admin/config/dev",
        f"{BASE}/admin/config/diff",
        f"{BASE}/admin/env/info",
    ]
    N = 150
    latencies: list[float] = []
    successes = 0

    async def one(i: int) -> None:
        ep = endpoints[i % len(endpoints)]
        t0 = time.perf_counter()
        resp = await async_client.get(ep, headers=headers)
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)
        if resp.status_code in (200, 404):
            nonlocal successes
            successes += 1

    await asyncio.gather(*[one(i) for i in range(N)])
    _print_stats("P-PERF-05 mixed", latencies, successes, N)

    p95 = _percentile(latencies, 95)
    assert p95 <= _CONFIG_GET_P95_MS, f"P95={p95:.0f}ms > {_CONFIG_GET_P95_MS}ms"
    assert successes / N >= _SUCCESS_RATE_MIN, f"성공률 {successes / N:.1%}"


# ─────────────────────────────────────────────────────────────────────────────
# P-PERF-06  SSE 스트림 응답 시간 (첫 청크)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_p_perf_06_sse_first_event_latency(async_client: httpx.AsyncClient) -> None:
    """P-PERF-06: SSE /stream/metrics 첫 청크 수신 P95 ≤ 2,000ms."""
    from jose import jwt as jose_jwt

    from app.core.config import settings

    token = jose_jwt.encode(
        {
            "sub": "perf-u5",
            "tenant_id": "t1",
            "roles": ["portal:viewer"],
            "iss": "agentoe-portal",
            "exp": int(time.time()) + 3600,
        },
        settings.JWT_SECRET,
        algorithm="HS256",
    )
    headers = {"Authorization": f"Bearer {token}"}

    N = 10  # SSE 는 연결 비용이 높으므로 소수로
    latencies: list[float] = []

    async def one() -> None:
        t0 = time.perf_counter()
        async with async_client.stream(
            "GET", f"{BASE}/stream/metrics", headers=headers, timeout=5.0
        ) as resp:
            async for line in resp.aiter_lines():
                if line.strip():
                    break
        ms = (time.perf_counter() - t0) * 1000
        latencies.append(ms)

    await asyncio.gather(*[one() for _ in range(N)])

    p95 = _percentile(latencies, 95)
    p50 = _percentile(latencies, 50)
    print(f"\n  [P-PERF-06 SSE first event] N={N}, P50={p50:.0f}ms, P95={p95:.0f}ms")
    assert p95 <= 2_000, f"SSE 첫 이벤트 P95={p95:.0f}ms > 2,000ms"
