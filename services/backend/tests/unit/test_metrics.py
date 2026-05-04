"""
Unit tests for app/core/metrics.py

테스트 범위:
  - _SlidingHistogram: observe / percentile / stats / reset
  - _Counter: inc / get
  - _Gauge: set / inc / dec
  - record_pipeline_call: 카운터 + 히스토그램 동시 업데이트
  - record_transfer_request / record_policy_block
  - inc/dec_active_sessions 게이지
  - get_pipeline_stats: 테넌트별 분리 조회
  - get_all_metrics: 통합 반환 구조
  - generate_prometheus_metrics: 텍스트 포맷 검증
  - P95 계산 정확도 (통계적 허용 오차 내)
"""
from __future__ import annotations

import math
import sys
import unittest.mock as mock

import pytest

# 외부 의존성 없이 metrics 모듈만 import
for mod in ["motor", "motor.motor_asyncio", "pymongo", "redis", "redis.asyncio"]:
    if mod not in sys.modules:
        sys.modules[mod] = mock.MagicMock()

import app.core.metrics as m
from app.core.metrics import (
    _Counter,
    _Gauge,
    _MetricsStore,
    _SlidingHistogram,
    dec_active_sessions,
    generate_prometheus_metrics,
    get_all_metrics,
    get_pipeline_stats,
    inc_active_sessions,
    record_pipeline_call,
    record_policy_block,
    record_transfer_request,
    set_circuit_breaker_state,
)


# ── 픽스처 ─────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_store():
    """각 테스트 전 메트릭 스토어 초기화."""
    m._store = _MetricsStore()
    yield
    m._store = _MetricsStore()


# ── _SlidingHistogram ─────────────────────────────────────────────────────────


def test_histogram_empty_stats():
    h = _SlidingHistogram()
    s = h.stats()
    assert s["p50"] == 0.0
    assert s["p95"] == 0.0
    assert s["count"] == 0


def test_histogram_single_value():
    h = _SlidingHistogram()
    h.observe(100.0)
    s = h.stats()
    assert s["p50"] == 100.0
    assert s["p95"] == 100.0
    assert s["avg"] == 100.0
    assert s["max"] == 100.0
    assert s["count"] == 1


def test_histogram_percentile_accuracy():
    """100개 값에서 P95가 정확한지 검증."""
    h = _SlidingHistogram()
    values = list(range(1, 101))  # 1~100
    for v in values:
        h.observe(float(v))

    s = h.stats()
    # P95 = 100개 중 95번째 = 95 (±2 허용)
    assert 93 <= s["p95"] <= 97
    # P50 = 50번째 = 50 (±2 허용)
    assert 48 <= s["p50"] <= 52


def test_histogram_window_bounded():
    """window=10 초과 시 오래된 값 제거."""
    h = _SlidingHistogram(window=10)
    for i in range(20):
        h.observe(float(i))
    assert len(h._samples) == 10
    # 최근 10개: 10~19
    assert min(h._samples) >= 10


def test_histogram_reset_window():
    h = _SlidingHistogram()
    h.observe(500.0)
    h.reset_window()
    assert len(h._samples) == 0
    # count_total은 리셋 안 됨 (전체 카운트 유지)
    assert h._count_total == 1


def test_histogram_percentile_empty_returns_zero():
    h = _SlidingHistogram()
    assert h.percentile(0.95) == 0.0


# ── _Counter ──────────────────────────────────────────────────────────────────


def test_counter_starts_at_zero():
    c = _Counter()
    assert c.get() == 0.0


def test_counter_inc():
    c = _Counter()
    c.inc()
    c.inc(5.0)
    assert c.get() == 6.0


# ── _Gauge ────────────────────────────────────────────────────────────────────


def test_gauge_set():
    g = _Gauge()
    g.set(42.0)
    assert g.get() == 42.0


def test_gauge_inc_dec():
    g = _Gauge()
    g.inc(3)
    g.dec(1)
    assert g.get() == 2.0


def test_gauge_dec_floor_at_zero():
    g = _Gauge()
    g.dec(10)
    assert g.get() == 0.0


# ── record_pipeline_call ──────────────────────────────────────────────────────


def test_record_pipeline_success():
    record_pipeline_call("t1", success=True, total_ms=500.0,
                         stt_ms=100.0, llm_ms=250.0, tts_ms=100.0)

    stats = get_pipeline_stats("t1")["t1"]
    assert stats["calls"]["success"] == 1
    assert stats["calls"]["error"] == 0
    assert stats["calls"]["total"] == 1
    assert stats["pipeline_latency_ms"]["count"] == 1
    assert stats["stt_latency_ms"]["count"] == 1
    assert stats["llm_latency_ms"]["count"] == 1
    assert stats["tts_latency_ms"]["count"] == 1


def test_record_pipeline_error():
    record_pipeline_call("t1", success=False, total_ms=3000.0)

    stats = get_pipeline_stats("t1")["t1"]
    assert stats["calls"]["error"] == 1
    assert stats["error_rate"] == 1.0


def test_record_pipeline_degraded():
    record_pipeline_call("t1", success=False, total_ms=400.0, degraded=True)

    stats = get_pipeline_stats("t1")["t1"]
    assert stats["calls"]["degraded"] == 1


def test_pipeline_error_rate_calculation():
    record_pipeline_call("t1", success=True, total_ms=400.0)
    record_pipeline_call("t1", success=True, total_ms=400.0)
    record_pipeline_call("t1", success=False, total_ms=400.0)

    stats = get_pipeline_stats("t1")["t1"]
    assert stats["calls"]["total"] == 3
    assert abs(stats["error_rate"] - 1 / 3) < 0.01


def test_pipeline_stats_tenant_isolation():
    """테넌트별 메트릭 격리."""
    record_pipeline_call("t1", success=True, total_ms=300.0)
    record_pipeline_call("t2", success=False, total_ms=999.0)

    stats_t1 = get_pipeline_stats("t1").get("t1", {})
    stats_t2 = get_pipeline_stats("t2").get("t2", {})

    assert stats_t1.get("calls", {}).get("success", 0) == 1
    assert stats_t2.get("calls", {}).get("error", 0) == 1


# ── record_transfer_request / policy_block ────────────────────────────────────


def test_record_transfer_request():
    record_transfer_request("t1", "CUSTOMER_REQUEST")
    record_transfer_request("t1", "G4_POLICY")
    record_transfer_request("t1", "CUSTOMER_REQUEST")

    from app.core.metrics import get_transfer_stats
    stats = get_transfer_stats("t1")
    assert stats["t1"]["CUSTOMER_REQUEST"] == 2.0
    assert stats["t1"]["G4_POLICY"] == 1.0


def test_record_policy_block():
    record_policy_block("t1", "G3")
    record_policy_block("t1", "G3")
    record_policy_block("t1", "G5")

    # policy_blocks 내부 스토어 직접 확인
    assert m._store.policy_blocks["t1:G3"].get() == 2.0
    assert m._store.policy_blocks["t1:G5"].get() == 1.0


# ── active_sessions 게이지 ────────────────────────────────────────────────────


def test_active_sessions_gauge():
    inc_active_sessions("t1")
    inc_active_sessions("t1")
    inc_active_sessions("t2")
    dec_active_sessions("t1")

    from app.core.metrics import get_active_sessions
    active = get_active_sessions()
    assert active["t1"] == 1.0
    assert active["t2"] == 1.0


def test_active_sessions_no_negative():
    dec_active_sessions("t1")  # 0에서 감소
    from app.core.metrics import get_active_sessions
    assert get_active_sessions().get("t1", 0.0) == 0.0


# ── circuit_breaker 게이지 ────────────────────────────────────────────────────


def test_set_circuit_breaker_state():
    set_circuit_breaker_state("groq-stt", 2)  # OPEN
    from app.core.metrics import get_circuit_breaker_gauges
    assert get_circuit_breaker_gauges()["groq-stt"] == 2.0


# ── get_all_metrics 구조 검증 ─────────────────────────────────────────────────


def test_get_all_metrics_structure():
    record_pipeline_call("t1", success=True, total_ms=400.0)
    result = get_all_metrics("t1")

    assert "timestamp" in result
    assert "pipeline" in result
    assert "transfers" in result
    assert "active_sessions" in result
    assert "circuit_breakers" in result
    assert "latency_budget_ms" in result
    assert isinstance(result["circuit_breakers"], list)


# ── Prometheus 텍스트 포맷 ────────────────────────────────────────────────────


def test_prometheus_output_contains_metrics():
    record_pipeline_call("t1", success=True, total_ms=300.0, stt_ms=80.0)
    inc_active_sessions("t1")

    content, content_type = generate_prometheus_metrics()

    assert "agentoe_pipeline_calls_total" in content
    assert "agentoe_pipeline_latency_ms" in content
    assert "agentoe_active_sessions" in content
    assert "text/plain" in content_type


def test_prometheus_output_has_labels():
    record_pipeline_call("tenant-abc", success=True, total_ms=200.0)

    content, _ = generate_prometheus_metrics()
    assert 'tenant="tenant-abc"' in content


def test_prometheus_output_empty_store():
    """메트릭 없어도 헤더 라인은 존재해야 함."""
    content, _ = generate_prometheus_metrics()
    assert "# HELP" in content
    assert "# TYPE" in content
