"""
gRPC server 메트릭 — vbgw bridge ↔ backend 통신 관측.

설계:
  - prometheus_client 선택적 import (개발 환경 호환).
  - core/metrics.py 는 agentic 파이프라인 중심. 여기는 transport (gRPC) 중심.
  - 시리즈 이름 prefix `agentoe_grpc_*` — kube-prometheus-stack 의 SLO 시리즈와 충돌 X.
  - 추가로 SLO doc 의 `agentoe_call_setup_total`, `agentoe_call_terminations_total`,
    `agentoe_call_duration_seconds` 도 backend 가 노출 (vbgw 가 stable 해질 때까지
    backend 가 백업 발화원). 라벨/이름 docs/reference/slo.md 와 정확히 일치.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

try:
    from prometheus_client import Counter, Gauge, Histogram

    GRPC_SESSIONS_ACTIVE = Gauge(
        "agentoe_grpc_sessions_active",
        "Active VoicebotAiService bidi streams on this Pod",
        ["tenant"],
    )
    GRPC_SESSIONS_TOTAL = Counter(
        "agentoe_grpc_sessions_total",
        "VoicebotAiService streams started",
        ["tenant"],
    )
    GRPC_SESSIONS_ENDED = Counter(
        "agentoe_grpc_sessions_ended_total",
        "VoicebotAiService streams ended by reason",
        ["tenant", "reason"],
    )
    GRPC_AUDIO_CHUNKS = Counter(
        "agentoe_grpc_audio_chunks_total",
        "AudioChunks received from bridge",
        ["tenant"],
    )

    # ── SLO doc 시리즈 — vbgw 가 미노출일 때를 대비한 backend 측 백업 발화 ─
    CALL_SETUP_TOTAL = Counter(
        "agentoe_call_setup_total",
        "Call setup attempts by result (backend gRPC view)",
        ["result"],  # ok | fail
    )
    CALL_TERMINATIONS_TOTAL = Counter(
        "agentoe_call_terminations_total",
        "Call terminations by reason (backend gRPC view)",
        ["reason"],
    )
    CALL_DURATION_SECONDS = Histogram(
        "agentoe_call_duration_seconds",
        "Call duration in seconds (backend gRPC view)",
        buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
    )
    _PROM_OK = True
except ImportError:  # pragma: no cover
    _PROM_OK = False


def grpc_session_start(tenant: str) -> None:
    if _PROM_OK:
        GRPC_SESSIONS_TOTAL.labels(tenant=tenant).inc()
        GRPC_SESSIONS_ACTIVE.labels(tenant=tenant).inc()


def grpc_session_end(tenant: str, reason: str) -> None:
    if _PROM_OK:
        GRPC_SESSIONS_ACTIVE.labels(tenant=tenant).dec()
        GRPC_SESSIONS_ENDED.labels(tenant=tenant, reason=reason).inc()


def grpc_chunk_received(tenant: str) -> None:
    if _PROM_OK:
        GRPC_AUDIO_CHUNKS.labels(tenant=tenant).inc()


def record_call_setup(result: str) -> None:
    """SLO 시리즈 발화. result ∈ {ok, fail}."""
    if _PROM_OK:
        CALL_SETUP_TOTAL.labels(result=result).inc()


def record_call_termination(reason: str) -> None:
    """SLO 시리즈 발화. reason ∈ {normal, client_hangup, network, server_error, crash, timeout}."""
    if _PROM_OK:
        CALL_TERMINATIONS_TOTAL.labels(reason=reason).inc()


def record_call_duration(seconds: float) -> None:
    if _PROM_OK:
        CALL_DURATION_SECONDS.observe(seconds)
