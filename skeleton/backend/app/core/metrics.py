"""
AgentOE 중앙 메트릭 레지스트리

설계 원칙:
  - prometheus_client 선택적 의존성: 미설치 시 in-process 저장소로 동작
  - 모든 측정값은 thread-safe / async-safe (asyncio.Lock 없이 GIL 보호)
  - 외부 Prometheus 스크래퍼 없이도 JSON API로 실시간 조회 가능

측정 지표:
  Counters:
    pipeline_calls_total{tenant, status}      — 파이프라인 호출 횟수
    stt_calls_total{tenant, status}           — STT 호출
    llm_calls_total{tenant, status}           — LLM 호출
    tts_calls_total{tenant, status}           — TTS 호출
    transfer_requests_total{tenant, reason}   — 이관 요청
    policy_blocks_total{tenant, level}        — PolicyGate 차단

  Histograms (분포):
    pipeline_latency_ms{tenant}               — 전체 파이프라인 레이턴시
    stt_latency_ms{tenant}                    — STT 레이턴시
    llm_latency_ms{tenant}                    — LLM 레이턴시
    tts_latency_ms{tenant}                    — TTS 레이턴시

  Gauges (현재값):
    active_sessions{tenant}                   — 활성 세션 수
    circuit_breaker_state{service}            — CB 상태 (0=CLOSED, 1=HALF_OPEN, 2=OPEN)

P50/P95 계산:
  - 슬라이딩 윈도우 방식: 최근 1000개 샘플 유지
  - 메모리 효율을 위해 collections.deque 사용
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

# Prometheus client 선택적 임포트
try:
    from prometheus_client import (
        REGISTRY,
        Counter,
        Gauge,
        Histogram,
        generate_latest,
        CONTENT_TYPE_LATEST,
    )
    _PROMETHEUS_AVAILABLE = True
except ImportError:
    _PROMETHEUS_AVAILABLE = False

# ── 버킷 정의 (ms 단위) ────────────────────────────────────────────────────────
LATENCY_BUCKETS_MS = [50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000]

# 슬라이딩 윈도우 크기
WINDOW_SIZE = 1000


# ── in-process 히스토그램 구현 ─────────────────────────────────────────────────

class _SlidingHistogram:
    """
    외부 의존성 없는 in-process 슬라이딩 윈도우 히스토그램.
    P50/P95/P99 + 평균/최대값 계산 지원.
    """

    def __init__(self, window: int = WINDOW_SIZE) -> None:
        self._samples: deque[float] = deque(maxlen=window)
        self._lock = Lock()
        self._count_total: int = 0
        self._sum_total: float = 0.0

    def observe(self, value: float) -> None:
        with self._lock:
            self._samples.append(value)
            self._count_total += 1
            self._sum_total += value

    def percentile(self, p: float) -> float:
        """p: 0.0 ~ 1.0. 샘플 없으면 0.0 반환."""
        with self._lock:
            if not self._samples:
                return 0.0
            sorted_s = sorted(self._samples)
            idx = math.ceil(p * len(sorted_s)) - 1
            return sorted_s[max(0, idx)]

    def stats(self) -> dict[str, float]:
        with self._lock:
            if not self._samples:
                return {"p50": 0.0, "p95": 0.0, "p99": 0.0,
                        "avg": 0.0, "max": 0.0,
                        "count": self._count_total, "sum": self._sum_total}
            sorted_s = sorted(self._samples)
            n = len(sorted_s)
            return {
                "p50": sorted_s[max(0, math.ceil(0.50 * n) - 1)],
                "p95": sorted_s[max(0, math.ceil(0.95 * n) - 1)],
                "p99": sorted_s[max(0, math.ceil(0.99 * n) - 1)],
                "avg": sum(sorted_s) / n,
                "max": sorted_s[-1],
                "count": self._count_total,
                "sum": self._sum_total,
            }

    def reset_window(self) -> None:
        with self._lock:
            self._samples.clear()


class _Counter:
    """Thread-safe in-process counter."""

    def __init__(self) -> None:
        self._value: float = 0.0
        self._lock = Lock()

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def get(self) -> float:
        return self._value


class _Gauge:
    """Thread-safe in-process gauge (set/inc/dec)."""

    def __init__(self, initial: float = 0.0) -> None:
        self._value = initial
        self._lock = Lock()

    def set(self, value: float) -> None:
        with self._lock:
            self._value = value

    def inc(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value += amount

    def dec(self, amount: float = 1.0) -> None:
        with self._lock:
            self._value = max(0.0, self._value - amount)

    def get(self) -> float:
        return self._value


# ── 메트릭 레지스트리 ─────────────────────────────────────────────────────────

@dataclass
class _MetricsStore:
    """in-process 메트릭 저장소. 전역 싱글턴으로 사용."""

    # Counters (label → _Counter)
    pipeline_calls: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    stt_calls: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    llm_calls: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    tts_calls: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    transfer_requests: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    policy_blocks: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )

    # Histograms (tenant_id → _SlidingHistogram)
    pipeline_latency: defaultdict[str, _SlidingHistogram] = field(
        default_factory=lambda: defaultdict(_SlidingHistogram)
    )
    stt_latency: defaultdict[str, _SlidingHistogram] = field(
        default_factory=lambda: defaultdict(_SlidingHistogram)
    )
    llm_latency: defaultdict[str, _SlidingHistogram] = field(
        default_factory=lambda: defaultdict(_SlidingHistogram)
    )
    tts_latency: defaultdict[str, _SlidingHistogram] = field(
        default_factory=lambda: defaultdict(_SlidingHistogram)
    )

    # Gauges
    active_sessions: defaultdict[str, _Gauge] = field(
        default_factory=lambda: defaultdict(_Gauge)
    )
    circuit_breaker_state: defaultdict[str, _Gauge] = field(
        default_factory=lambda: defaultdict(_Gauge)
    )


_store = _MetricsStore()


# ── 공개 API ──────────────────────────────────────────────────────────────────

# ── Pipeline ─────────────────────────────────────────────────────────────────

def record_pipeline_call(
    tenant_id: str,
    success: bool,
    total_ms: float,
    stt_ms: float = 0.0,
    llm_ms: float = 0.0,
    tts_ms: float = 0.0,
    degraded: bool = False,
) -> None:
    """AI 파이프라인 1회 완료 후 호출."""
    status = "degraded" if degraded else ("success" if success else "error")
    _store.pipeline_calls[f"{tenant_id}:{status}"].inc()
    _store.pipeline_latency[tenant_id].observe(total_ms)
    if stt_ms > 0:
        _store.stt_latency[tenant_id].observe(stt_ms)
    if llm_ms > 0:
        _store.llm_latency[tenant_id].observe(llm_ms)
    if tts_ms > 0:
        _store.tts_latency[tenant_id].observe(tts_ms)


def record_stt_call(tenant_id: str, success: bool, duration_ms: float) -> None:
    status = "success" if success else "error"
    _store.stt_calls[f"{tenant_id}:{status}"].inc()
    _store.stt_latency[tenant_id].observe(duration_ms)


def record_llm_call(tenant_id: str, success: bool, duration_ms: float) -> None:
    status = "success" if success else "error"
    _store.llm_calls[f"{tenant_id}:{status}"].inc()
    _store.llm_latency[tenant_id].observe(duration_ms)


def record_tts_call(tenant_id: str, success: bool, duration_ms: float) -> None:
    status = "success" if success else "error"
    _store.tts_calls[f"{tenant_id}:{status}"].inc()
    _store.tts_latency[tenant_id].observe(duration_ms)


def record_transfer_request(tenant_id: str, reason: str) -> None:
    _store.transfer_requests[f"{tenant_id}:{reason}"].inc()


def record_policy_block(tenant_id: str, level: str) -> None:
    _store.policy_blocks[f"{tenant_id}:{level}"].inc()


# ── Session Gauge ──────────────────────────────────────────────────────────────

def inc_active_sessions(tenant_id: str) -> None:
    _store.active_sessions[tenant_id].inc()


def dec_active_sessions(tenant_id: str) -> None:
    _store.active_sessions[tenant_id].dec()


def set_circuit_breaker_state(service_name: str, state_value: int) -> None:
    """state_value: 0=CLOSED, 1=HALF_OPEN, 2=OPEN"""
    _store.circuit_breaker_state[service_name].set(float(state_value))


# ── 조회 API ──────────────────────────────────────────────────────────────────

def get_pipeline_stats(tenant_id: str | None = None) -> dict[str, Any]:
    """tenant_id 지정 시 해당 테넌트 통계, None이면 전체 합산."""
    tenants = (
        [tenant_id] if tenant_id
        else list({k.split(":")[0] for k in _store.pipeline_calls})
        or ["_global"]
    )

    result: dict[str, Any] = {}
    for t in tenants:
        calls_success = _store.pipeline_calls[f"{t}:success"].get()
        calls_error = _store.pipeline_calls[f"{t}:error"].get()
        calls_degraded = _store.pipeline_calls[f"{t}:degraded"].get()
        total = calls_success + calls_error + calls_degraded
        result[t] = {
            "calls": {
                "total": total,
                "success": calls_success,
                "error": calls_error,
                "degraded": calls_degraded,
            },
            "error_rate": round(calls_error / max(total, 1), 4),
            "pipeline_latency_ms": _store.pipeline_latency[t].stats(),
            "stt_latency_ms": _store.stt_latency[t].stats(),
            "llm_latency_ms": _store.llm_latency[t].stats(),
            "tts_latency_ms": _store.tts_latency[t].stats(),
        }
    return result


def get_transfer_stats(tenant_id: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for label, counter in _store.transfer_requests.items():
        t, reason = label.split(":", 1)
        if tenant_id and t != tenant_id:
            continue
        result.setdefault(t, {})[reason] = counter.get()
    return result


def get_active_sessions() -> dict[str, float]:
    return {t: g.get() for t, g in _store.active_sessions.items()}


def get_circuit_breaker_gauges() -> dict[str, float]:
    return {svc: g.get() for svc, g in _store.circuit_breaker_state.items()}


def get_all_metrics(tenant_id: str | None = None) -> dict[str, Any]:
    """모든 메트릭 통합 반환 (JSON API 응답용)."""
    from app.domain.circuit_breaker import get_all_statuses
    cb_statuses = get_all_statuses()

    # CB 상태를 게이지로 동기화
    _CB_STATE_MAP = {"CLOSED": 0, "HALF_OPEN": 1, "OPEN": 2}
    for cb in cb_statuses:
        set_circuit_breaker_state(
            cb["service"],
            _CB_STATE_MAP.get(cb["state"], 0),
        )

    return {
        "timestamp": time.time(),
        "pipeline": get_pipeline_stats(tenant_id),
        "transfers": get_transfer_stats(tenant_id),
        "active_sessions": get_active_sessions(),
        "circuit_breakers": cb_statuses,
        "latency_budget_ms": {
            "stt": 500,
            "llm": 500,
            "tts": 300,
            "total": 2500,
        },
    }


# ── Prometheus 텍스트 포맷 출력 ───────────────────────────────────────────────

def generate_prometheus_metrics() -> tuple[str, str]:
    """
    Prometheus scrape 엔드포인트용 텍스트 포맷 생성.
    prometheus_client 미설치 시 in-process 데이터로 간이 포맷 생성.
    반환: (content, content_type)
    """
    if _PROMETHEUS_AVAILABLE:
        return generate_latest(REGISTRY).decode(), CONTENT_TYPE_LATEST

    # 간이 Prometheus text format
    lines: list[str] = []
    ts_ms = int(time.time() * 1000)

    def _counter_line(name: str, labels: dict, value: float) -> str:
        lstr = ",".join(f'{k}="{v}"' for k, v in labels.items())
        return f"{name}{{{lstr}}} {value} {ts_ms}"

    # pipeline_calls_total
    lines.append("# HELP agentoe_pipeline_calls_total Total AI pipeline calls")
    lines.append("# TYPE agentoe_pipeline_calls_total counter")
    for label, ctr in _store.pipeline_calls.items():
        tenant, status = label.split(":", 1)
        lines.append(_counter_line(
            "agentoe_pipeline_calls_total",
            {"tenant": tenant, "status": status},
            ctr.get(),
        ))

    # pipeline latency summary (P50/P95)
    lines.append("# HELP agentoe_pipeline_latency_ms Pipeline latency in milliseconds")
    lines.append("# TYPE agentoe_pipeline_latency_ms summary")
    for tenant, hist in _store.pipeline_latency.items():
        s = hist.stats()
        for q, val in [("0.5", s["p50"]), ("0.95", s["p95"]), ("0.99", s["p99"])]:
            lines.append(_counter_line(
                "agentoe_pipeline_latency_ms",
                {"tenant": tenant, "quantile": q},
                val,
            ))
        lines.append(f'agentoe_pipeline_latency_ms_count{{tenant="{tenant}"}} {s["count"]}')
        lines.append(f'agentoe_pipeline_latency_ms_sum{{tenant="{tenant}"}} {s["sum"]:.2f}')

    # active_sessions
    lines.append("# HELP agentoe_active_sessions Current active WebSocket sessions")
    lines.append("# TYPE agentoe_active_sessions gauge")
    for tenant, gauge in _store.active_sessions.items():
        lines.append(f'agentoe_active_sessions{{tenant="{tenant}"}} {gauge.get()}')

    # circuit_breaker_state
    lines.append("# HELP agentoe_circuit_breaker_state CB state (0=CLOSED,1=HALF_OPEN,2=OPEN)")
    lines.append("# TYPE agentoe_circuit_breaker_state gauge")
    for svc, gauge in _store.circuit_breaker_state.items():
        lines.append(f'agentoe_circuit_breaker_state{{service="{svc}"}} {gauge.get()}')

    return "\n".join(lines) + "\n", "text/plain; version=0.0.4; charset=utf-8"
