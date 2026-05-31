"""
Metrics & Monitoring API

엔드포인트:
  GET  /api/v1/metrics/pipeline     — AI 파이프라인 P50/P95/P99 레이턴시 + 호출 통계
  GET  /api/v1/metrics/sessions     — 활성 세션 수 + 세션 KPI
  GET  /api/v1/metrics/ai           — Circuit Breaker 상태 + 벤더별 레이턴시
  GET  /api/v1/metrics/transfers    — 이관 요청 통계
  GET  /api/v1/metrics/summary      — 전체 메트릭 통합 (운영 대시보드용)
  GET  /api/v1/metrics/prometheus   — Prometheus scrape 엔드포인트 (인증 없음)

접근 제어:
  - /prometheus: 인증 불필요 (Prometheus scraper 접근용)
  - 나머지: JWT 필요 (operator 이상)
"""

from __future__ import annotations

from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from app.core.auth import TenantContext, get_current_tenant
from app.core.metrics import (
    generate_prometheus_metrics,
    get_active_sessions,
    get_all_metrics,
    get_pipeline_stats,
    get_transfer_stats,
)
from app.domain.circuit_breaker import get_all_statuses

logger = structlog.get_logger(__name__)

router = APIRouter()


# ── 파이프라인 레이턴시 ────────────────────────────────────────────────────────


@router.get("/pipeline")
async def pipeline_metrics(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> dict[str, Any]:
    """
    AI 파이프라인 P50/P95/P99 레이턴시 및 성공/에러율.

    응답 예시:
    {
      "tenant_id": "tenant-001",
      "pipeline": {
        "calls": {"total": 1000, "success": 990, "error": 5, "degraded": 5},
        "error_rate": 0.005,
        "pipeline_latency_ms": {"p50": 420, "p95": 1850, "p99": 2300, "avg": 680},
        "stt_latency_ms": {...},
        "llm_latency_ms": {...},
        "tts_latency_ms": {...}
      },
      "latency_budget_ms": {"stt": 500, "llm": 500, "tts": 300, "total": 2500},
      "budget_compliance": {"stt_p95_ok": true, "total_p95_ok": true}
    }
    """
    stats = get_pipeline_stats(tenant.tenant_id)
    tenant_stats = stats.get(tenant.tenant_id, {})

    # 레이턴시 예산 준수 여부 계산
    pl = tenant_stats.get("pipeline_latency_ms", {})
    stt = tenant_stats.get("stt_latency_ms", {})
    llm = tenant_stats.get("llm_latency_ms", {})
    tts = tenant_stats.get("tts_latency_ms", {})

    budget_compliance = {
        "stt_p95_ok": stt.get("p95", 0) <= 500,
        "llm_p95_ok": llm.get("p95", 0) <= 500,
        "tts_p95_ok": tts.get("p95", 0) <= 300,
        "total_p95_ok": pl.get("p95", 0) <= 2500,
    }

    return {
        "tenant_id": tenant.tenant_id,
        "pipeline": tenant_stats,
        "latency_budget_ms": {"stt": 500, "llm": 500, "tts": 300, "total": 2500},
        "budget_compliance": budget_compliance,
    }


# ── 세션 KPI ───────────────────────────────────────────────────────────────────


@router.get("/sessions")
async def session_metrics(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> dict[str, Any]:
    """
    활성 세션 수 + 세션 KPI.

    active_sessions: 현재 연결된 WebSocket 세션 수 (in-memory gauge)
    """
    active = get_active_sessions()
    tenant_active = active.get(tenant.tenant_id, 0)

    return {
        "tenant_id": tenant.tenant_id,
        "active_sessions": int(tenant_active),
        "all_tenants_active": {t: int(v) for t, v in active.items()},
    }


# ── AI 서비스 (Circuit Breaker) ────────────────────────────────────────────────


@router.get("/ai")
async def ai_metrics(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> dict[str, Any]:
    """
    STT/LLM/TTS Circuit Breaker 상태 + 벤더별 레이턴시.

    circuit_breaker state: CLOSED(정상) / HALF_OPEN(복구중) / OPEN(차단)
    error_rate: 전체 호출 대비 실패율
    """
    cb_statuses = get_all_statuses()
    stats = get_pipeline_stats(tenant.tenant_id).get(tenant.tenant_id, {})

    return {
        "tenant_id": tenant.tenant_id,
        "circuit_breakers": cb_statuses,
        "vendor_latency_ms": {
            "stt": stats.get("stt_latency_ms", {}),
            "llm": stats.get("llm_latency_ms", {}),
            "tts": stats.get("tts_latency_ms", {}),
        },
        "latency_budget_ms": {
            "stt": 500,
            "llm_first_token": 500,
            "tts": 300,
            "total": 2500,
        },
    }


# ── 이관 통계 ──────────────────────────────────────────────────────────────────


@router.get("/transfers")
async def transfer_metrics(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> dict[str, Any]:
    """
    테넌트별 상담사 이관 요청 통계.
    reason별 breakdown (CUSTOMER_REQUEST / G4_POLICY / REPEATED_FAILURE 등)
    """
    stats = get_transfer_stats(tenant.tenant_id)
    tenant_stats = stats.get(tenant.tenant_id, {})
    total = sum(tenant_stats.values())

    return {
        "tenant_id": tenant.tenant_id,
        "total_transfers": total,
        "by_reason": tenant_stats,
    }


# ── 전체 통합 요약 ─────────────────────────────────────────────────────────────


@router.get("/summary")
async def metrics_summary(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> dict[str, Any]:
    """
    운영 대시보드용 전체 메트릭 통합 뷰.
    pipeline + sessions + circuit_breakers + transfers 통합 반환.
    """
    all_data = get_all_metrics(tenant.tenant_id)
    logger.info("Metrics summary requested", tenant_id=tenant.tenant_id)
    return {
        "tenant_id": tenant.tenant_id,
        **all_data,
    }


# ── Prometheus Scrape 엔드포인트 (인증 없음) ──────────────────────────────────


@router.get("/prometheus", include_in_schema=False)
async def prometheus_metrics() -> PlainTextResponse:
    """
    Prometheus text format 메트릭 스크래핑 엔드포인트.
    인증 없음 — Prometheus 서버에서 직접 접근.
    prometheus.yml scrape_configs:
      - job_name: agentoe
        static_configs:
          - targets: ['agentoe:8000']
        metrics_path: /api/v1/metrics/prometheus
    """
    content, content_type = generate_prometheus_metrics()
    return PlainTextResponse(content=content, media_type=content_type)
