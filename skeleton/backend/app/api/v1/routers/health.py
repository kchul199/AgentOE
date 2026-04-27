"""Health check endpoints — K8s probe 규격 분리.

K8s 각 probe 역할:
  /livez    — 프로세스가 살아있기만 하면 200 (재시작 트리거)
  /readyz   — Mongo/Redis 및 downstream 준비 + drain 중이 아닐 때만 200
               (트래픽 공급 대상에서 제외 트리거)
  /startupz — 초기화 완료 여부 (slow-start 애플리케이션 보호)

기존 /health는 하위호환으로 유지 (대시보드/로드밸런서에서 사용 중).
"""
from __future__ import annotations

import time

import structlog
from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.graceful_shutdown import shutdown_manager

logger = structlog.get_logger(__name__)
router = APIRouter()

_startup_completed_at: float | None = None


def mark_startup_complete() -> None:
    global _startup_completed_at
    _startup_completed_at = time.time()


async def _check_mongo() -> tuple[bool, str]:
    try:
        from app.core.database import get_db  # type: ignore

        db = get_db()
        # ping은 1-RTT 미만 — 핫패스 허용 범위
        await db.command("ping")
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, f"mongo: {e}"


async def _check_redis() -> tuple[bool, str]:
    try:
        from app.core.redis_client import get_redis

        await get_redis().ping()
        return True, "ok"
    except Exception as e:  # noqa: BLE001
        return False, f"redis: {e}"


@router.get("/livez")
async def livez() -> dict:
    """Liveness: 프로세스가 응답하면 성공. drain 중에도 200."""
    return {"status": "alive", "version": settings.VERSION}


@router.get("/readyz")
async def readyz() -> JSONResponse:
    """
    Readiness: 트래픽 수용 가능 여부.
    - drain 플래그가 켜지면 503 → K8s가 Service 엔드포인트에서 제외
    - Mongo/Redis 실패 시 503
    """
    if shutdown_manager.is_draining:
        return JSONResponse(
            status_code=503,
            content={"status": "draining", "reason": "graceful_shutdown_in_progress"},
        )

    mongo_ok, mongo_msg = await _check_mongo()
    redis_ok, redis_msg = await _check_redis()

    body = {
        "status": "ready" if (mongo_ok and redis_ok) else "not_ready",
        "checks": {"mongodb": mongo_msg, "redis": redis_msg},
        "version": settings.VERSION,
    }
    return JSONResponse(status_code=200 if (mongo_ok and redis_ok) else 503, content=body)


@router.get("/startupz")
async def startupz() -> JSONResponse:
    """Startup: 초기화 완료 여부. lifespan startup 말미에서 mark_startup_complete() 호출 필요."""
    if _startup_completed_at is None:
        return JSONResponse(status_code=503, content={"status": "starting"})
    return JSONResponse(
        status_code=200,
        content={"status": "started", "uptime_s": round(time.time() - _startup_completed_at, 2)},
    )


@router.get("/health")
async def health_check() -> dict:
    """하위 호환용. 신규 배포는 /livez /readyz /startupz 사용 권장."""
    return {"status": "ok", "version": settings.VERSION, "env": settings.ENVIRONMENT}
