"""AgentOE FastAPI Application Entry Point.

v2 패치 요약:
  - CORS 허용 메서드/헤더 명시 (['*'] 제거 — CSRF 대응)
  - RateLimitMiddleware 추가 (Redis 토큰 버킷, 분당)
  - Graceful Shutdown 핸들러 등록 (SIGTERM → readiness drain → 강제 종료)
  - 미들웨어 순서: Logging(최외곽) → KillSwitch → RateLimit → Admission → 애플리케이션
"""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import router as api_v1_router
from app.api.v1.routers.health import mark_startup_complete
from app.core.config import settings
from app.core.database import close_db, init_db
from app.core.exceptions import AgentOEBaseError
from app.core.graceful_shutdown import register_signal_handlers, shutdown_manager
from app.core.logging import setup_logging
from app.core.redis_client import close_redis, init_redis
from app.grpc_server import GrpcServerLifecycle
from app.middleware.admission_middleware import AdmissionControlMiddleware
from app.middleware.http_metrics_middleware import HTTPMetricsMiddleware
from app.middleware.idempotency_middleware import IdempotencyMiddleware
from app.middleware.kill_switch_middleware import KillSwitchMiddleware
from app.middleware.logging_middleware import LoggingMiddleware
from app.middleware.rate_limit_middleware import RateLimitMiddleware
from app.repositories.session_repository import SessionRepository

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown events."""
    setup_logging()
    await init_db()
    await init_redis()
    register_signal_handlers()

    # gRPC 서버 — vbgw bridge 가 VoicebotAiService 호출.
    # Mongo/Redis init 후, mark_startup_complete 전에 시작 — readiness 가 grpc port
    # 도 함께 검증할 수 있게.
    grpc_lifecycle: GrpcServerLifecycle | None = None
    if getattr(settings, "GRPC_ENABLED", True):
        grpc_lifecycle = GrpcServerLifecycle(repo=SessionRepository())
        await grpc_lifecycle.start()
        app.state.grpc_lifecycle = grpc_lifecycle

    # Readiness 프로브(/startupz)가 "초기화 완료"를 감지하도록 마지막에 표시
    mark_startup_complete()
    logger.info(
        "AgentOE API started",
        version=settings.VERSION,
        env=settings.ENVIRONMENT,
        rate_limit=settings.RATE_LIMIT_ENABLED,
        pii_masking=settings.PII_MASKING_ENABLED,
        grpc_enabled=grpc_lifecycle is not None,
    )
    try:
        yield
    finally:
        # gRPC graceful drain — HTTP/Redis 닫기 전에 진행 중 통화 수용 시간 확보.
        if grpc_lifecycle is not None:
            try:
                await grpc_lifecycle.stop()
            except Exception as e:  # noqa: BLE001
                logger.error("grpc_shutdown_error", error=str(e))

        # Graceful: 신호를 못 받았더라도 drain을 시도 (uvicorn exit path)
        try:
            await shutdown_manager.drain()
        except Exception as e:  # noqa: BLE001
            logger.error("shutdown_drain_error", error=str(e))
        await close_redis()
        await close_db()
        logger.info("AgentOE API shutdown complete")


app = FastAPI(
    title="AgentOE API",
    description="Multi-tenant Agentic AI Voice Callbot Orchestration Platform",
    version=settings.VERSION,
    lifespan=lifespan,
    docs_url="/api/docs" if settings.ENVIRONMENT != "production" else None,
    redoc_url="/api/redoc" if settings.ENVIRONMENT != "production" else None,
)

# ── CORS (좁힌 설정) ────────────────────────────────────────────────────────
# 과거 allow_methods=['*'], allow_headers=['*']은 CSRF/사전요청 우회 위험.
# 명시 목록으로 좁히고 exposed headers 선언.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=[
        "Authorization",
        "Content-Type",
        "X-Request-Id",
        "X-Tenant-Id",
        "X-Trace-Id",
    ],
    expose_headers=["X-Request-Id", "X-Trace-Id"],
    max_age=600,
)

# 미들웨어는 역순 실행 — 아래에 붙는 것이 먼저 실행됨(Starlette 규약).
# 원하는 순서: HTTPMetrics → Logging → KillSwitch → RateLimit → Idempotency → Admission → app
#   * HTTPMetrics 는 가장 outer — handler timing 측정에 다른 미들웨어 비용도 모두 포함
#   * Idempotency 가 RateLimit 안쪽 — 429 는 캐시하지 않는다
#   * Admission 안쪽 — 동시성 lease 와 dedup 모두 적용
app.add_middleware(AdmissionControlMiddleware)  # inner-most before router
app.add_middleware(IdempotencyMiddleware)
app.add_middleware(RateLimitMiddleware)
app.add_middleware(KillSwitchMiddleware)
app.add_middleware(LoggingMiddleware)           # 로그/트레이스 진입
app.add_middleware(HTTPMetricsMiddleware)       # outer-most (SLO timing — 모든 inner 비용 포함)


@app.exception_handler(AgentOEBaseError)
async def agentoe_error_handler(request, exc: AgentOEBaseError) -> JSONResponse:
    """Global handler for AgentOE custom exceptions."""
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": exc.code, "message": exc.message},
    )


app.include_router(api_v1_router, prefix="/api/v1")
