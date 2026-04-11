"""
구조화 로깅 — structlog + context var 기반 자동 주입

설계 원칙:
  - 모든 로그에 session_id / tenant_id / request_id 자동 포함
  - LoggingMiddleware가 요청 진입 시 bind_request_context() 호출
  - 파이프라인 / WebSocket 핸들러에서 bind_session_context() 추가 바인딩
  - 운영 환경: JSON Lines (ELK/Cloud Logging 직접 수집)
  - 개발 환경: 컬러 ConsoleRenderer

사용법:
    import structlog
    logger = structlog.get_logger()
    logger.info("pipeline complete", latency_ms=320)
    # → {"event":"pipeline complete","latency_ms":320,"session_id":"...","tenant_id":"..."}

    # 요청 진입 시 (middleware):
    from app.core.logging import bind_request_context
    bind_request_context(request_id="...", tenant_id="...")

    # WebSocket 연결 시:
    from app.core.logging import bind_session_context
    bind_session_context(session_id="...", tenant_id="...")
"""
from __future__ import annotations

import logging
import sys
import uuid

import structlog
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    unbind_contextvars,
)

from app.core.config import settings


# ── 로깅 설정 ──────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """애플리케이션 시작 시 1회 호출. structlog 전역 설정."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # stdlib logging 기본 설정
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # 운영/개발 환경별 렌더러 선택
    if settings.ENVIRONMENT == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            # 1. contextvars에 바인딩된 값 병합 (session_id, tenant_id 등)
            structlog.contextvars.merge_contextvars,
            # 2. stdlib 로그 레벨 추가
            structlog.processors.add_log_level,
            # 3. 스택 트레이스 포매팅
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            # 4. ISO 8601 타임스탬프
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # 5. 최종 렌더링
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ── Context var 헬퍼 ──────────────────────────────────────────────────────────

def bind_request_context(
    request_id: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    path: str | None = None,
    method: str | None = None,
) -> str:
    """
    HTTP 요청 진입 시 호출. structlog context var에 요청 메타데이터 바인딩.
    반환값: 생성된 request_id
    """
    rid = request_id or str(uuid.uuid4())[:8]
    ctx: dict = {"request_id": rid}
    if tenant_id:
        ctx["tenant_id"] = tenant_id
    if client_id:
        ctx["client_id"] = client_id
    if path:
        ctx["path"] = path
    if method:
        ctx["method"] = method
    bind_contextvars(**ctx)
    return rid


def bind_session_context(
    session_id: str,
    tenant_id: str | None = None,
    client_id: str | None = None,
) -> None:
    """
    WebSocket 연결 / 세션 시작 시 호출.
    이후 해당 코루틴에서 발생하는 모든 로그에 session_id 자동 포함.
    """
    ctx: dict = {"session_id": session_id}
    if tenant_id:
        ctx["tenant_id"] = tenant_id
    if client_id:
        ctx["client_id"] = client_id
    bind_contextvars(**ctx)


def bind_pipeline_context(
    stage: str,
    policy_level: str | None = None,
) -> None:
    """AI 파이프라인 단계별 context 바인딩."""
    ctx: dict = {"pipeline_stage": stage}
    if policy_level:
        ctx["policy_level"] = policy_level
    bind_contextvars(**ctx)


def clear_request_context() -> None:
    """요청 종료 시 context var 정리 (미들웨어 finally에서 호출)."""
    clear_contextvars()


def unbind_keys(*keys: str) -> None:
    """특정 키만 바인딩 해제."""
    unbind_contextvars(*keys)
