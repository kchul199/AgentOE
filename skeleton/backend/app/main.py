"""AgentOE FastAPI Application Entry Point."""
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.v1 import router as api_v1_router
from app.core.config import settings
from app.core.database import close_db, init_db
from app.core.exceptions import AgentOEBaseError
from app.core.logging import setup_logging
from app.core.redis_client import close_redis, init_redis
from app.middleware.kill_switch_middleware import KillSwitchMiddleware
from app.middleware.logging_middleware import LoggingMiddleware

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown events."""
    setup_logging()
    await init_db()
    await init_redis()
    logger.info("AgentOE API started", version=settings.VERSION, env=settings.ENVIRONMENT)
    yield
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(KillSwitchMiddleware)
# LoggingMiddleware: 요청별 request_id/tenant_id context var 자동 주입
# KillSwitch 이후 등록 → 정상 요청에만 로깅 적용
app.add_middleware(LoggingMiddleware)


@app.exception_handler(AgentOEBaseError)
async def agentoe_error_handler(request, exc: AgentOEBaseError) -> JSONResponse:
    """Global handler for AgentOE custom exceptions."""
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": exc.code, "message": exc.message},
    )


app.include_router(api_v1_router, prefix="/api/v1")
