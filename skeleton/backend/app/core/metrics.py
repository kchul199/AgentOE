"""
AgentOE 중앙 메트릭 레지스트리

설계 원칙:
  - prometheus_client 선택적 의존성:
      설치된 경우  → Prometheus 네이티브 객체(Counter/Histogram/Gauge)에 이중 기록.
                    /metrics 스크랩 시 실제 AgentOE 지표가 노출됩니다.
      미설치된 경우 → in-process _store만 사용. 간이 Prometheus 텍스트 포맷 생성.
  - JSON API: 항상 in-process _store에서 읽습니다 (prometheus_client 설치 여부 무관).
  - Prometheus 멀티-Pod 집계:
      각 Pod가 독립적으로 /metrics를 노출하며, Prometheus Operator가 전체를 수집합니다.
      전체 합계: sum(agentoe_pipeline_calls_total) by (tenant, status)
      활성 세션: sum(agentoe_active_sessions) by (tenant)
  - active_sessions Gauge:
      in-process 게이지는 이 Pod의 세션 수를 나타냅니다.
      Pod 재시작 시 0으로 리셋되는 것이 정상 동작입니다.
      어드미션 카운터(Redis)는 한도 강제용이며, 메트릭과 용도가 다릅니다.
  - stats() avg: sum_total / count_total (전체 누적 평균)으로 통일.
      count, sum과 같은 기간의 값이므로 avg = sum / count 검증이 항상 성립합니다.

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
    active_sessions{tenant}                   — 이 Pod의 활성 세션 수
    circuit_breaker_state{service}            — CB 상태 (0=CLOSED, 1=HALF_OPEN, 2=OPEN)

P50/P95/P99 계산:
  - 슬라이딩 윈도우 방식: 최근 1000개 샘플 유지 (Percentile은 윈도우 기반)
  - avg/count/sum은 서비스 시작 이후 전체 누적값
"""
from __future__ import annotations

import math
import time
from collections import defaultdict, deque
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

# ── Prometheus client 선택적 임포트 ───────────────────────────────────────────
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

# JWKS refresh 는 네트워크 I/O(수십 ms ~ 수 초). 초 단위 버킷 별도.
JWKS_REFRESH_DURATION_BUCKETS_S = [0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0]

# 슬라이딩 윈도우 크기
WINDOW_SIZE = 1000


# ── in-process 히스토그램 구현 ─────────────────────────────────────────────────

class _SlidingHistogram:
    """
    외부 의존성 없는 in-process 슬라이딩 윈도우 히스토그램.
    - Percentile (P50/P95/P99): 최근 WINDOW_SIZE 샘플 기반
    - avg / count / sum: 서비스 시작 이후 전체 누적값 (avg = sum / count 항상 성립)
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
        """
        통계 반환.
        - p50/p95/p99/max: 슬라이딩 윈도우(최근 WINDOW_SIZE) 기반
        - avg/count/sum:   전체 누적값 (avg = sum / count 항상 성립)
        """
        with self._lock:
            if not self._samples:
                return {
                    "p50": 0.0, "p95": 0.0, "p99": 0.0,
                    "avg": 0.0, "max": 0.0,
                    "count": self._count_total,
                    "sum": self._sum_total,
                }
            sorted_s = sorted(self._samples)
            n = len(sorted_s)
            return {
                "p50": sorted_s[max(0, math.ceil(0.50 * n) - 1)],
                "p95": sorted_s[max(0, math.ceil(0.95 * n) - 1)],
                "p99": sorted_s[max(0, math.ceil(0.99 * n) - 1)],
                # avg = sum_total / count_total — count/sum과 동일 기간
                "avg": self._sum_total / max(self._count_total, 1),
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
    """Thread-safe in-process gauge (set/inc/dec). dec() 최솟값 0."""

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


# ── in-process 메트릭 저장소 ──────────────────────────────────────────────────

@dataclass
class _MetricsStore:
    """
    In-process 메트릭 저장소.
    - JSON API(/api/v1/status) 응답에 항상 사용됩니다.
    - prometheus_client 설치 여부와 무관하게 존재합니다.
    """

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
    # Track 3: LLM quota / token / cost
    # label = f"{tenant}:{scope}:{result}" — scope ∈ {tokens, cost, none}
    #                                        result ∈ {ok, warn, fallback, reject}
    llm_quota_checks: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    # label = f"{tenant}:{model}"
    llm_tokens_consumed: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    llm_cost_cents: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )
    # Track 3: JWKS cache lookups — label = result ∈ {hit, miss, force_refresh, fail}
    jwks_lookups: defaultdict[str, _Counter] = field(
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
    # Track 3: JWKS refresh duration (초 단위). label = result ∈ {success, failure}
    jwks_refresh_duration_s: defaultdict[str, _SlidingHistogram] = field(
        default_factory=lambda: defaultdict(_SlidingHistogram)
    )

    # Gauges
    active_sessions: defaultdict[str, _Gauge] = field(
        default_factory=lambda: defaultdict(_Gauge)
    )
    circuit_breaker_state: defaultdict[str, _Gauge] = field(
        default_factory=lambda: defaultdict(_Gauge)
    )
    # Track 2 P2: WebSocket back-pressure
    # label = tenant
    ws_send_queue_depth: defaultdict[str, _Gauge] = field(
        default_factory=lambda: defaultdict(_Gauge)
    )
    # label = f"{tenant}:{kind}" — kind ∈ {audio, event, full}
    ws_drops: defaultdict[str, _Counter] = field(
        default_factory=lambda: defaultdict(_Counter)
    )


# ── Prometheus 네이티브 메트릭 객체 ───────────────────────────────────────────
# prometheus_client가 설치된 경우에만 생성됩니다.
# 각 함수에서 _store와 동시에 기록 (이중 기록).
# _prom이 None이면 in-process만 사용합니다.

_prom: Any = None  # _PrometheusMetrics | None

if _PROMETHEUS_AVAILABLE:
    class _PrometheusMetrics:
        """
        Prometheus 네이티브 메트릭 객체 묶음.

        멀티-Pod 환경에서 Prometheus는 각 Pod의 /metrics를 개별 스크랩합니다.
        PromQL 집계 예시:
          sum(agentoe_pipeline_calls_total) by (tenant, status)
          histogram_quantile(0.95, sum(rate(agentoe_pipeline_latency_ms_bucket[5m])) by (le, tenant))
          sum(agentoe_active_sessions) by (tenant)
        """
        def __init__(self) -> None:
            self.pipeline_calls = Counter(
                "agentoe_pipeline_calls_total",
                "AI pipeline calls by tenant and status",
                ["tenant", "status"],
            )
            self.pipeline_latency = Histogram(
                "agentoe_pipeline_latency_ms",
                "Pipeline end-to-end latency in milliseconds",
                ["tenant"],
                buckets=LATENCY_BUCKETS_MS,
            )
            self.stt_calls = Counter(
                "agentoe_stt_calls_total",
                "STT (Groq Whisper) calls by tenant and status",
                ["tenant", "status"],
            )
            self.stt_latency = Histogram(
                "agentoe_stt_latency_ms",
                "STT latency in milliseconds",
                ["tenant"],
                buckets=LATENCY_BUCKETS_MS,
            )
            self.llm_calls = Counter(
                "agentoe_llm_calls_total",
                "LLM (Groq Llama) calls by tenant and status",
                ["tenant", "status"],
            )
            self.llm_latency = Histogram(
                "agentoe_llm_latency_ms",
                "LLM latency in milliseconds",
                ["tenant"],
                buckets=LATENCY_BUCKETS_MS,
            )
            self.tts_calls = Counter(
                "agentoe_tts_calls_total",
                "TTS (Google Neural2) calls by tenant and status",
                ["tenant", "status"],
            )
            self.tts_latency = Histogram(
                "agentoe_tts_latency_ms",
                "TTS latency in milliseconds",
                ["tenant"],
                buckets=LATENCY_BUCKETS_MS,
            )
            self.transfer_requests = Counter(
                "agentoe_transfer_requests_total",
                "Agent transfer requests by tenant and reason",
                ["tenant", "reason"],
            )
            self.policy_blocks = Counter(
                "agentoe_policy_blocks_total",
                "PolicyGate blocks by tenant and policy level",
                ["tenant", "level"],
            )
            self.active_sessions = Gauge(
                "agentoe_active_sessions",
                "Active WebSocket sessions on this Pod (sum across pods with PromQL)",
                ["tenant"],
            )
            self.circuit_breaker_state = Gauge(
                "agentoe_circuit_breaker_state",
                "Circuit breaker state: 0=CLOSED, 1=HALF_OPEN, 2=OPEN",
                ["service"],
            )
            # ── Track 3: LLM quota / token / cost ─────────────────────────
            self.llm_quota_checks = Counter(
                "agentoe_llm_quota_checks_total",
                "LLM daily quota check results by tenant, scope, and outcome",
                ["tenant", "scope", "result"],
            )
            self.llm_tokens_consumed = Counter(
                "agentoe_llm_tokens_consumed_total",
                "Total LLM tokens consumed by tenant and model",
                ["tenant", "model"],
            )
            self.llm_cost_cents = Counter(
                "agentoe_llm_cost_cents_total",
                "Total LLM cost in cents by tenant and model",
                ["tenant", "model"],
            )
            # ── Track 3: JWKS cache + rotation ────────────────────────────
            self.jwks_lookups = Counter(
                "agentoe_jwks_lookups_total",
                "JWKS cache lookups by outcome (hit/miss/force_refresh/fail)",
                ["result"],
            )
            self.jwks_refresh_duration = Histogram(
                "agentoe_jwks_refresh_duration_seconds",
                "JWKS remote refresh duration in seconds",
                ["result"],
                buckets=JWKS_REFRESH_DURATION_BUCKETS_S,
            )
            # ── Track 2 P2: WebSocket back-pressure ────────────────────────
            self.ws_send_queue_depth = Gauge(
                "agentoe_ws_send_queue_depth",
                "Current depth of per-session WebSocket send queue (this Pod)",
                ["tenant"],
            )
            self.ws_drops = Counter(
                "agentoe_ws_drops_total",
                "Dropped WebSocket outbound events by reason",
                ["tenant", "kind"],
            )

    _prom = _PrometheusMetrics()


# ── 전역 저장소 인스턴스 ──────────────────────────────────────────────────────
_store = _MetricsStore()


# ── 공개 기록 API ─────────────────────────────────────────────────────────────
# 모든 함수는 _store에 기록합니다 (JSON API용).
# _prom이 존재하면 동시에 Prometheus 네이티브 객체에도 기록합니다 (/metrics 스크랩용).

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

    # ── in-process (JSON API) ─────────────────────────────────────────────
    _store.pipeline_calls[f"{tenant_id}:{status}"].inc()
    _store.pipeline_latency[tenant_id].observe(total_ms)
    if stt_ms > 0:
        _store.stt_latency[tenant_id].observe(stt_ms)
    if llm_ms > 0:
        _store.llm_latency[tenant_id].observe(llm_ms)
    if tts_ms > 0:
        _store.tts_latency[tenant_id].observe(tts_ms)

    # ── Prometheus 네이티브 (/metrics) ────────────────────────────────────
    if _prom:
        _prom.pipeline_calls.labels(tenant=tenant_id, status=status).inc()
        _prom.pipeline_latency.labels(tenant=tenant_id).observe(total_ms)
        if stt_ms > 0:
            _prom.stt_latency.labels(tenant=tenant_id).observe(stt_ms)
        if llm_ms > 0:
            _prom.llm_latency.labels(tenant=tenant_id).observe(llm_ms)
        if tts_ms > 0:
            _prom.tts_latency.labels(tenant=tenant_id).observe(tts_ms)


def record_stt_call(tenant_id: str, success: bool, duration_ms: float) -> None:
    status = "success" if success else "error"
    _store.stt_calls[f"{tenant_id}:{status}"].inc()
    _store.stt_latency[tenant_id].observe(duration_ms)
    if _prom:
        _prom.stt_calls.labels(tenant=tenant_id, status=status).inc()
        _prom.stt_latency.labels(tenant=tenant_id).observe(duration_ms)


def record_llm_call(tenant_id: str, success: bool, duration_ms: float) -> None:
    status = "success" if success else "error"
    _store.llm_calls[f"{tenant_id}:{status}"].inc()
    _store.llm_latency[tenant_id].observe(duration_ms)
    if _prom:
        _prom.llm_calls.labels(tenant=tenant_id, status=status).inc()
        _prom.llm_latency.labels(tenant=tenant_id).observe(duration_ms)


def record_tts_call(tenant_id: str, success: bool, duration_ms: float) -> None:
    status = "success" if success else "error"
    _store.tts_calls[f"{tenant_id}:{status}"].inc()
    _store.tts_latency[tenant_id].observe(duration_ms)
    if _prom:
        _prom.tts_calls.labels(tenant=tenant_id, status=status).inc()
        _prom.tts_latency.labels(tenant=tenant_id).observe(duration_ms)


def record_transfer_request(tenant_id: str, reason: str) -> None:
    _store.transfer_requests[f"{tenant_id}:{reason}"].inc()
    if _prom:
        _prom.transfer_requests.labels(tenant=tenant_id, reason=reason).inc()


def record_policy_block(tenant_id: str, level: str) -> None:
    _store.policy_blocks[f"{tenant_id}:{level}"].inc()
    if _prom:
        _prom.policy_blocks.labels(tenant=tenant_id, level=level).inc()


# ── Session Gauge ─────────────────────────────────────────────────────────────

def inc_active_sessions(tenant_id: str) -> None:
    _store.active_sessions[tenant_id].inc()
    if _prom:
        _prom.active_sessions.labels(tenant=tenant_id).inc()


def dec_active_sessions(tenant_id: str) -> None:
    _store.active_sessions[tenant_id].dec()
    if _prom:
        _prom.active_sessions.labels(tenant=tenant_id).dec()


def set_circuit_breaker_state(service_name: str, state_value: int) -> None:
    """state_value: 0=CLOSED, 1=HALF_OPEN, 2=OPEN"""
    _store.circuit_breaker_state[service_name].set(float(state_value))
    if _prom:
        _prom.circuit_breaker_state.labels(service=service_name).set(state_value)


# ── Track 3: LLM 쿼터 / 사용량 ────────────────────────────────────────────────

# scope: "tokens" | "cost" | "none"  (쿼터 소진이 어느 축인지, 정상이면 "none")
# result: "ok" | "warn" | "fallback" | "reject"
_VALID_QUOTA_SCOPES = {"tokens", "cost", "none"}
_VALID_QUOTA_RESULTS = {"ok", "warn", "fallback", "reject"}


def record_quota_check(tenant_id: str, scope: str, result: str) -> None:
    """
    LLM 일일 쿼터 체크 1회 결과 기록.

    enforce_quota() 분기마다 정확히 1회 호출:
      - under quota              → scope="none", result="ok"
      - over, policy="warn"      → scope="tokens"|"cost", result="warn"
      - over, policy="fallback"  → scope="tokens"|"cost", result="fallback"
      - over, policy="reject"    → scope="tokens"|"cost", result="reject"

    레이블 카디널리티 보호: 알 수 없는 값은 조용히 "none"/"ok" 으로 정규화.
    """
    if scope not in _VALID_QUOTA_SCOPES:
        scope = "none"
    if result not in _VALID_QUOTA_RESULTS:
        result = "ok"
    _store.llm_quota_checks[f"{tenant_id}:{scope}:{result}"].inc()
    if _prom:
        _prom.llm_quota_checks.labels(
            tenant=tenant_id, scope=scope, result=result,
        ).inc()


def record_llm_usage(
    tenant_id: str,
    model: str,
    tokens: int,
    cost_cents: float,
) -> None:
    """
    LLM 호출 1회의 실제 사용량 기록 (commit_usage 와 같은 시점).

    tokens/cost_cents 가 0 이하여도 inc(0) 은 무해하지만,
    레이블 생성 비용이 있으므로 둘 다 0 이면 스킵.
    """
    if tokens <= 0 and cost_cents <= 0:
        return
    label = f"{tenant_id}:{model}"
    if tokens > 0:
        _store.llm_tokens_consumed[label].inc(float(tokens))
    if cost_cents > 0:
        _store.llm_cost_cents[label].inc(float(cost_cents))
    if _prom:
        if tokens > 0:
            _prom.llm_tokens_consumed.labels(
                tenant=tenant_id, model=model,
            ).inc(float(tokens))
        if cost_cents > 0:
            _prom.llm_cost_cents.labels(
                tenant=tenant_id, model=model,
            ).inc(float(cost_cents))


# ── Track 3: JWKS 캐시 / 회전 ─────────────────────────────────────────────────

_VALID_JWKS_LOOKUP_RESULTS = {"hit", "miss", "force_refresh", "fail"}
_VALID_JWKS_REFRESH_RESULTS = {"success", "failure"}


def record_jwks_lookup(result: str) -> None:
    """
    JWKS 캐시 조회 결과 1회 기록.

    result:
      - "hit"            — 캐시 TTL 유효, 즉시 반환
      - "miss"           — 캐시 만료/없음 → 원격 fetch 트리거
      - "force_refresh"  — kid 매칭 실패로 1회 강제 refresh
      - "fail"           — stale 조차 없고 fetch 도 실패
    """
    if result not in _VALID_JWKS_LOOKUP_RESULTS:
        return
    _store.jwks_lookups[result].inc()
    if _prom:
        _prom.jwks_lookups.labels(result=result).inc()


def record_jwks_refresh(duration_s: float, success: bool) -> None:
    """
    JWKS 원격 refresh 1회의 소요 시간 기록.

    - success=True  → 정상 응답 (200 + 파싱 성공)
    - success=False → 네트워크 에러 / 5xx / 파싱 실패 (30s 백오프 진입)
    """
    result = "success" if success else "failure"
    _store.jwks_refresh_duration_s[result].observe(duration_s)
    if _prom:
        _prom.jwks_refresh_duration.labels(result=result).observe(duration_s)


# ── Track 2 P2: WebSocket back-pressure ──────────────────────────────────────

# 큐 overflow 시 drop 사유.
#   audio : drop-oldest 로 기존 오디오 청크 하나를 버리고 새 이벤트 채택
#   event : drop-newest 로 이번 비-오디오 이벤트를 버림 (큐 유지)
#   full  : 큐에 drop 대상도 없는 경계 케이스 (방어)
_VALID_WS_DROP_KINDS = {"audio", "event", "full"}


def set_ws_queue_depth(tenant_id: str, depth: int) -> None:
    """
    세션당 WS 송신 큐의 현재 점유율을 게이지에 반영.

    호출 빈도가 높으므로(enqueue/drain 마다) 가벼워야 함 — 불필요한 할당 없이
    기존 _Gauge 인스턴스의 set() 만 호출. label = tenant_id.

    주의: 이 게이지는 "이 Pod" 의 값이며, Prometheus 측에서는
    agentoe_ws_send_queue_depth{tenant="..."} 로 Pod 별로 노출된다.
    전체 보기에는 sum by (tenant) (agentoe_ws_send_queue_depth) 사용.
    """
    _store.ws_send_queue_depth[tenant_id].set(float(depth))
    if _prom:
        _prom.ws_send_queue_depth.labels(tenant=tenant_id).set(float(depth))


def record_ws_drop(tenant_id: str, kind: str) -> None:
    """
    WS 송신 큐 overflow 로 이벤트 1개를 drop 했을 때 호출.

    kind 검증:
      알 수 없는 값은 카디널리티 보호를 위해 조용히 무시한다
      (record_quota_check 와 동일 정책). 이는 프로덕션에서 오탈자가
      Prometheus label polluation 으로 번지지 않게 하기 위함.
    """
    if kind not in _VALID_WS_DROP_KINDS:
        return
    _store.ws_drops[f"{tenant_id}:{kind}"].inc()
    if _prom:
        _prom.ws_drops.labels(tenant=tenant_id, kind=kind).inc()


# ── 조회 API (항상 in-process _store 기반) ────────────────────────────────────

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
        calls_error   = _store.pipeline_calls[f"{t}:error"].get()
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
            "stt_latency_ms":      _store.stt_latency[t].stats(),
            "llm_latency_ms":      _store.llm_latency[t].stats(),
            "tts_latency_ms":      _store.tts_latency[t].stats(),
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

    # CB 상태를 게이지로 동기화 (_store + _prom 이중 기록)
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

    prometheus_client 설치된 경우:
      → generate_latest(REGISTRY) 반환.
        모든 record_* 함수가 Prometheus 네이티브 객체에 이중 기록하므로
        AgentOE 지표가 정상적으로 포함됩니다.

    prometheus_client 미설치:
      → in-process _store 데이터로 간이 Prometheus 텍스트 포맷 생성.
        개발 환경에서 Prometheus 없이도 /metrics 엔드포인트 동작 확인 가능.
    """
    if _PROMETHEUS_AVAILABLE:
        # _prom 객체에 이중 기록된 실제 AgentOE 메트릭이 REGISTRY에 포함됩니다
        return generate_latest(REGISTRY).decode(), CONTENT_TYPE_LATEST

    # ── 간이 Prometheus text format (in-process fallback) ─────────────────
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

    # pipeline latency summary (P50/P95/P99)
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
    lines.append("# HELP agentoe_active_sessions Active WebSocket sessions on this Pod")
    lines.append("# TYPE agentoe_active_sessions gauge")
    for tenant, gauge in _store.active_sessions.items():
        lines.append(f'agentoe_active_sessions{{tenant="{tenant}"}} {gauge.get()}')

    # circuit_breaker_state
    lines.append("# HELP agentoe_circuit_breaker_state CB state (0=CLOSED,1=HALF_OPEN,2=OPEN)")
    lines.append("# TYPE agentoe_circuit_breaker_state gauge")
    for svc, gauge in _store.circuit_breaker_state.items():
        lines.append(f'agentoe_circuit_breaker_state{{service="{svc}"}} {gauge.get()}')

    # ── Track 3: LLM quota check counters ─────────────────────────────────
    lines.append("# HELP agentoe_llm_quota_checks_total LLM daily quota check results")
    lines.append("# TYPE agentoe_llm_quota_checks_total counter")
    for label, ctr in _store.llm_quota_checks.items():
        parts = label.split(":", 2)
        if len(parts) != 3:
            continue
        tenant, scope, result = parts
        lines.append(_counter_line(
            "agentoe_llm_quota_checks_total",
            {"tenant": tenant, "scope": scope, "result": result},
            ctr.get(),
        ))

    # LLM tokens consumed
    lines.append("# HELP agentoe_llm_tokens_consumed_total Total LLM tokens consumed")
    lines.append("# TYPE agentoe_llm_tokens_consumed_total counter")
    for label, ctr in _store.llm_tokens_consumed.items():
        tenant, model = label.split(":", 1)
        lines.append(_counter_line(
            "agentoe_llm_tokens_consumed_total",
            {"tenant": tenant, "model": model},
            ctr.get(),
        ))

    # LLM cost cents
    lines.append("# HELP agentoe_llm_cost_cents_total Total LLM cost in cents")
    lines.append("# TYPE agentoe_llm_cost_cents_total counter")
    for label, ctr in _store.llm_cost_cents.items():
        tenant, model = label.split(":", 1)
        lines.append(_counter_line(
            "agentoe_llm_cost_cents_total",
            {"tenant": tenant, "model": model},
            ctr.get(),
        ))

    # ── Track 3: JWKS cache + refresh ─────────────────────────────────────
    lines.append("# HELP agentoe_jwks_lookups_total JWKS cache lookups by outcome")
    lines.append("# TYPE agentoe_jwks_lookups_total counter")
    for result, ctr in _store.jwks_lookups.items():
        lines.append(_counter_line(
            "agentoe_jwks_lookups_total",
            {"result": result},
            ctr.get(),
        ))

    lines.append("# HELP agentoe_jwks_refresh_duration_seconds JWKS refresh duration")
    lines.append("# TYPE agentoe_jwks_refresh_duration_seconds summary")
    for result, hist in _store.jwks_refresh_duration_s.items():
        s = hist.stats()
        for q, val in [("0.5", s["p50"]), ("0.95", s["p95"]), ("0.99", s["p99"])]:
            lines.append(_counter_line(
                "agentoe_jwks_refresh_duration_seconds",
                {"result": result, "quantile": q},
                val,
            ))
        lines.append(
            f'agentoe_jwks_refresh_duration_seconds_count{{result="{result}"}} '
            f'{s["count"]}'
        )
        lines.append(
            f'agentoe_jwks_refresh_duration_seconds_sum{{result="{result}"}} '
            f'{s["sum"]:.4f}'
        )

    # ── Track 2 P2: WS back-pressure ──────────────────────────────────────
    lines.append("# HELP agentoe_ws_send_queue_depth WS send queue depth (this Pod)")
    lines.append("# TYPE agentoe_ws_send_queue_depth gauge")
    for tenant, gauge in _store.ws_send_queue_depth.items():
        lines.append(
            f'agentoe_ws_send_queue_depth{{tenant="{tenant}"}} {gauge.get()}'
        )

    lines.append("# HELP agentoe_ws_drops_total WS outbound drops by kind")
    lines.append("# TYPE agentoe_ws_drops_total counter")
    for label, ctr in _store.ws_drops.items():
        parts = label.split(":", 1)
        if len(parts) != 2:
            continue
        tenant, kind = parts
        lines.append(_counter_line(
            "agentoe_ws_drops_total",
            {"tenant": tenant, "kind": kind},
            ctr.get(),
        ))

    return "\n".join(lines) + "\n", "text/plain; version=0.0.4; charset=utf-8"
