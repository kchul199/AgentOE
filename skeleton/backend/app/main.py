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

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown events."""
    setup_logging()
    await init_db()
    logger.info("AgentOE API started", version=settings.VERSION, env=settings.ENVIRONMENT)
    yield
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


@app.exception_handler(AgentOEBaseError)
async def agentoe_error_handler(request, exc: AgentOEBaseError) -> JSONResponse:
    """Global handler for AgentOE custom exceptions."""
    return JSONResponse(
        status_code=exc.http_status,
        content={"error": exc.code, "message": exc.message},
    )


app.include_router(api_v1_router, prefix="/api/v1")
