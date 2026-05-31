"""Phase 5 확장 — 지속적 부하 테스트: N RPS × M 초

목표:
  - 일정 RPS 로 지속 부하를 주면서 SLO 준수 여부를 실시간 추적
  - 30초 단위 롤링 윈도우로 burn rate 계산
  - 에러 버짓 소진량 리포트

요청 구성 (10 콜/초):
  requests[i % 4] 순환:
    0 → GET /api/v1/metrics/pipeline    (인증 필요)
    1 → GET /api/v1/metrics/sessions    (인증 필요)
    2 → GET /api/v1/metrics/prometheus  (인증 불필요)
    3 → POST /api/v1/scenarios/validate (인증 필요, 유효 payload)

환경 변수:
  LOAD_DURATION_SECONDS   테스트 지속 시간 초 (기본값: 60 / 전체 10분: 600)
  LOAD_TARGET_RPS         목표 RPS         (기본값: 10)

실행 예시:
  # 기본 (60초 × 10 RPS = 600 요청):
  pytest tests/performance/test_load_sustained.py -v -s --no-cov

  # 전체 부하 (10분 × 10 RPS = 6,000 요청):
  LOAD_DURATION_SECONDS=600 pytest tests/performance/test_load_sustained.py -v -s --no-cov

SLO 기준 (docs/reference/slo.md):
  - 성공률 (non-5xx) ≥ 99.9%
  - P95 응답 지연 ≤ 500ms
  - 최대 burn rate (30초 윈도우) ≤ 14.4 (즉시 페이지콜 기준)
"""

from __future__ import annotations

import asyncio
import os
import statistics
import sys
import time
import unittest.mock as mock
from dataclasses import dataclass
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

# ── 테스트 파라미터 ────────────────────────────────────────────────────────────

LOAD_DURATION_SECONDS: int = int(os.environ.get("LOAD_DURATION_SECONDS", "60"))
LOAD_TARGET_RPS: int = int(os.environ.get("LOAD_TARGET_RPS", "10"))

# SLO 임계값 (docs/reference/slo.md)
SLO_SUCCESS_RATE: float = 0.999  # 99.9%
SLO_P95_MS: float = 500.0  # 500ms
BURN_RATE_PAGE_FAST: float = 14.4  # 5분/1h 즉시 페이지콜 임계
BURN_RATE_PAGE_SLOW: float = 6.0  # 30분/6h 페이지콜 임계

# ── 공통 패치 ─────────────────────────────────────────────────────────────────

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


# ── 픽스처 ────────────────────────────────────────────────────────────────────


@pytest.fixture(scope="module", autouse=True)
def reset_metrics():
    import app.core.metrics as m

    m._store = _MetricsStore()
    yield
    m._store = _MetricsStore()


@pytest.fixture(scope="module")
def asgi_app():
    """지속 부하 테스트용 패치된 ASGI 앱."""
    import app.main  # noqa: F401

    _mock_grpc = MagicMock()
    _mock_grpc.return_value.start = AsyncMock()
    _mock_grpc.return_value.stop = AsyncMock()

    _mock_repo = AsyncMock()
    _mock_repo.acquire_session_lease.return_value = True
    _mock_repo.release_session_lease = AsyncMock()

    patches = [
        *_COMMON_PATCHES,
        patch("app.main.init_db", new_callable=AsyncMock),
        patch("app.main.close_db", new_callable=AsyncMock),
        patch("app.main.init_redis", new_callable=AsyncMock),
        patch("app.main.close_redis", new_callable=AsyncMock),
        patch("app.main.GrpcServerLifecycle", new=_mock_grpc),
        patch("app.main.SessionRepository", return_value=_mock_repo),
    ]
    for p in patches:
        p.start()

    from app.main import app as _app

    yield _app

    for p in patches:
        p.stop()


@pytest.fixture(scope="module")
def auth_headers():
    token = create_access_token("load-tenant", "load-client", ["operator", "admin"])
    return {"Authorization": f"Bearer {token}"}


# ── 데이터 구조 ───────────────────────────────────────────────────────────────


@dataclass
class RequestRecord:
    """단일 요청 결과 레코드."""

    t_scheduled: float  # 요청이 발사될 예정이었던 시각 (monotonic)
    t_start: float  # 실제 발사 시각
    t_end: float  # 응답 완료 시각
    status_code: int  # HTTP 상태 코드 (실패 시 0)
    endpoint: str  # 경로

    @property
    def latency_ms(self) -> float:
        return (self.t_end - self.t_start) * 1000

    @property
    def is_error(self) -> bool:
        """5xx = 오류. 4xx 는 클라이언트 오류 — SLO 에서 성공으로 간주."""
        return self.status_code == 0 or self.status_code >= 500


@dataclass
class WindowStats:
    """30초 롤링 윈도우 통계."""

    window_start: float
    window_end: float
    n: int
    errors: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    mean_ms: float
    burn_rate: float  # (error_rate) / (1 - SLO)


# ── 유틸 ──────────────────────────────────────────────────────────────────────


def _percentile(sorted_ms: list[float], pct: float) -> float:
    if not sorted_ms:
        return 0.0
    k = (len(sorted_ms) - 1) * pct / 100
    lo, hi = int(k), min(int(k) + 1, len(sorted_ms) - 1)
    return sorted_ms[lo] + (sorted_ms[hi] - sorted_ms[lo]) * (k - lo)


def _compute_window_stats(
    records: list[RequestRecord], w_start: float, w_end: float
) -> WindowStats | None:
    """w_start ~ w_end 구간에 속하는 레코드로 윈도우 통계 계산."""
    subset = [r for r in records if w_start <= r.t_scheduled < w_end]
    if not subset:
        return None
    n = len(subset)
    errors = sum(1 for r in subset if r.is_error)
    lats = sorted(r.latency_ms for r in subset)
    error_rate = errors / n
    burn_rate = error_rate / (1 - SLO_SUCCESS_RATE) if (1 - SLO_SUCCESS_RATE) > 0 else 0.0
    return WindowStats(
        window_start=w_start,
        window_end=w_end,
        n=n,
        errors=errors,
        p50_ms=_percentile(lats, 50),
        p95_ms=_percentile(lats, 95),
        p99_ms=_percentile(lats, 99),
        mean_ms=statistics.mean(lats) if lats else 0.0,
        burn_rate=burn_rate,
    )


def _format_window(ws: WindowStats, elapsed: float) -> str:
    ok_flag = "✓" if (ws.errors == 0 and ws.p95_ms <= SLO_P95_MS) else "✗"
    burn_str = f"burn={ws.burn_rate:.2f}" if ws.burn_rate > 0 else "burn=0.00"
    return (
        f"  [{ok_flag}] t={elapsed:>5.0f}s  "
        f"n={ws.n:>3d}  err={ws.errors}  "
        f"P50={ws.p50_ms:>5.1f}ms  P95={ws.p95_ms:>5.1f}ms  P99={ws.p99_ms:>5.1f}ms  "
        f"mean={ws.mean_ms:>5.1f}ms  {burn_str}"
    )


# ── 부하 생성기 ───────────────────────────────────────────────────────────────

_ENDPOINTS = [
    ("GET", "/api/v1/metrics/pipeline"),
    ("GET", "/api/v1/metrics/sessions"),
    ("GET", "/api/v1/metrics/prometheus"),
    ("POST", "/api/v1/scenarios/validate"),
]

_VALIDATE_PAYLOAD = {
    "scenario_id": "load-test-scenario",
    "tenant_id": "load-tenant",
    "name": "부하테스트 시나리오",
    "description": "지속 부하 테스트용",
    "nodes": [
        {"id": "start", "type": "start", "transitions": [{"target": "greet"}]},
        {"id": "greet", "type": "speak", "text": "안녕하세요.", "transitions": [{"target": "end"}]},
        {"id": "end", "type": "end"},
    ],
}


async def _run_load(
    app,
    rps: int,
    duration_s: int,
    auth_hdrs: dict,
    *,
    report_interval_s: int = 30,
) -> list[RequestRecord]:
    """
    `rps` 개/초로 `duration_s` 초 동안 요청을 발사한다.

    타이밍 전략:
      - 각 요청은 발사 예정 시각(t0 + n/rps) 기준으로 스케줄됨.
      - asyncio.sleep 으로 예정 시각까지 대기 후 fire.
      - 요청 자체는 병렬 Task 로 실행 — 한 요청이 느려도 다음 요청은 정시 발사.
    """
    transport = httpx.ASGITransport(app=app)
    records: list[RequestRecord] = []
    lock = asyncio.Lock()

    origin = time.monotonic()
    interval = 1.0 / rps
    total_scheduled = rps * duration_s
    next_report_at = report_interval_s  # 첫 리포트 시각 (elapsed seconds)
    report_idx = 0

    print(
        f"\n{'═' * 72}\n"
        f"  지속 부하 테스트 시작\n"
        f"  목표: {rps} RPS × {duration_s}초 = {total_scheduled:,} 요청\n"
        f"  SLO: 성공률 ≥ {SLO_SUCCESS_RATE * 100:.1f}%  P95 ≤ {SLO_P95_MS:.0f}ms\n"
        f"{'─' * 72}"
    )

    async def _one(n: int, t_sched: float) -> None:
        method, path = _ENDPOINTS[n % len(_ENDPOINTS)]
        hdrs = {} if path.endswith("prometheus") else auth_hdrs
        t_start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                if method == "GET":
                    r = await client.get(path, headers=hdrs)
                else:
                    r = await client.post(path, headers=hdrs, json=_VALIDATE_PAYLOAD)
            code = r.status_code
        except Exception:
            code = 0
        t_end = time.monotonic()
        async with lock:
            records.append(
                RequestRecord(
                    t_scheduled=t_sched - origin,
                    t_start=t_start - origin,
                    t_end=t_end - origin,
                    status_code=code,
                    endpoint=path,
                )
            )

    tasks: list[asyncio.Task] = []
    for n in range(total_scheduled):
        t_fire = origin + n * interval
        sleep_s = t_fire - time.monotonic()
        if sleep_s > 0:
            await asyncio.sleep(sleep_s)

        task = asyncio.create_task(_one(n, t_fire))
        tasks.append(task)

        # 진행 상황 리포트
        elapsed = time.monotonic() - origin
        if elapsed >= next_report_at:
            # 완료된 레코드만 사용
            done_records = [r for r in records if r.t_scheduled < elapsed - 1]
            ws = _compute_window_stats(
                done_records,
                elapsed - report_interval_s,
                elapsed,
            )
            if ws:
                print(_format_window(ws, elapsed))
            next_report_at += report_interval_s
            report_idx += 1

    # 미완료 요청 수집 대기
    await asyncio.gather(*tasks)

    elapsed_total = time.monotonic() - origin
    print(f"{'─' * 72}")
    print(f"  총 실행 완료: {len(records):,}건 / {elapsed_total:.1f}초")
    return records


# ── 메인 테스트 ───────────────────────────────────────────────────────────────


@pytest.mark.slow
@pytest.mark.asyncio
async def test_sustained_load_slo_compliance(asgi_app, auth_headers):
    """
    {LOAD_TARGET_RPS} RPS × {LOAD_DURATION_SECONDS}초 지속 부하 → SLO 준수 검증.

    전체 10분 실행:
      LOAD_DURATION_SECONDS=600 pytest tests/performance/test_load_sustained.py -v -s --no-cov
    """
    records = await _run_load(
        asgi_app,
        rps=LOAD_TARGET_RPS,
        duration_s=LOAD_DURATION_SECONDS,
        auth_hdrs=auth_headers,
    )

    # ── 전체 집계 ──────────────────────────────────────────────────────────────
    total = len(records)
    total_errors = sum(1 for r in records if r.is_error)
    lats_sorted = sorted(r.latency_ms for r in records)

    p50 = _percentile(lats_sorted, 50)
    p95 = _percentile(lats_sorted, 95)
    p99 = _percentile(lats_sorted, 99)
    mean = statistics.mean(lats_sorted) if lats_sorted else 0.0
    success_rate = (total - total_errors) / total if total else 0.0
    error_rate = total_errors / total if total else 0.0
    burn_rate = error_rate / (1 - SLO_SUCCESS_RATE) if (1 - SLO_SUCCESS_RATE) > 0 else 0.0

    # 에러 버짓 (30일 기준 소진 %)
    budget_total_events = total
    budget_allowed_errors = budget_total_events * (1 - SLO_SUCCESS_RATE)
    budget_consumed_pct = (
        (total_errors / budget_allowed_errors * 100) if budget_allowed_errors > 0 else 0.0
    )

    # ── 30초 윈도우 burn rate 최대값 ──────────────────────────────────────────
    window_stats: list[WindowStats] = []
    w = 0
    while True:
        ws = _compute_window_stats(records, float(w), float(w + 30))
        if ws is None:
            break
        window_stats.append(ws)
        w += 30

    max_burn_rate = max((ws.burn_rate for ws in window_stats), default=0.0)

    # ── 엔드포인트별 분류 ─────────────────────────────────────────────────────
    by_endpoint: dict[str, list[RequestRecord]] = {}
    for r in records:
        by_endpoint.setdefault(r.endpoint, []).append(r)

    print(f"\n{'═' * 72}")
    print("  ★ 최종 결과 리포트")
    print(f"{'─' * 72}")
    print(f"  테스트 설정  : {LOAD_TARGET_RPS} RPS × {LOAD_DURATION_SECONDS}초")
    print(f"  총 요청수    : {total:,}")
    print(f"  총 오류수    : {total_errors:,} ({error_rate * 100:.3f}%)")
    print(f"  성공률       : {success_rate * 100:.3f}%  (SLO 기준: ≥{SLO_SUCCESS_RATE * 100:.1f}%)")
    print(f"  P50          : {p50:.1f}ms")
    print(f"  P95          : {p95:.1f}ms  (SLO 기준: ≤{SLO_P95_MS:.0f}ms)")
    print(f"  P99          : {p99:.1f}ms")
    print(f"  평균         : {mean:.1f}ms")
    print(f"  전체 Burn Rate: {burn_rate:.3f}  (14.4 이상 = 즉시 페이지콜)")
    print(f"  최대 Burn Rate (30초 창): {max_burn_rate:.3f}")
    print(f"  에러 버짓 소진: {budget_consumed_pct:.2f}%  (80% 이상 = freeze)")
    print(f"{'─' * 72}")
    print("  엔드포인트별 분류:")
    for ep, ep_recs in sorted(by_endpoint.items()):
        ep_lats = sorted(r.latency_ms for r in ep_recs)
        ep_err = sum(1 for r in ep_recs if r.is_error)
        ep_p95 = _percentile(ep_lats, 95)
        flag = "✓" if ep_err == 0 and ep_p95 <= SLO_P95_MS else "✗"
        print(f"  [{flag}] {ep:<45s}  n={len(ep_recs):>4d}  err={ep_err}  P95={ep_p95:>5.1f}ms")
    print(f"{'─' * 72}")

    if window_stats:
        print("  30초 윈도우 요약:")
        for ws in window_stats:
            print(_format_window(ws, ws.window_end))
    print(f"{'═' * 72}\n")

    # ── SLO 어서션 ────────────────────────────────────────────────────────────
    assert total > 0, "요청이 하나도 기록되지 않음"

    assert success_rate >= SLO_SUCCESS_RATE, (
        f"성공률 {success_rate * 100:.3f}% < SLO {SLO_SUCCESS_RATE * 100:.1f}%"
    )

    assert p95 <= SLO_P95_MS, f"P95 {p95:.1f}ms > SLO {SLO_P95_MS:.0f}ms"

    assert max_burn_rate < BURN_RATE_PAGE_FAST, (
        f"최대 Burn Rate {max_burn_rate:.2f} ≥ {BURN_RATE_PAGE_FAST} — 즉시 페이지콜 수준"
    )

    assert budget_consumed_pct < 80.0, (
        f"에러 버짓 {budget_consumed_pct:.1f}% 소진 — 80% 초과 시 배포 freeze 기준"
    )
