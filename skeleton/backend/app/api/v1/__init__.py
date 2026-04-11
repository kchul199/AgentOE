"""API v1 router aggregator."""
from fastapi import APIRouter

from app.api.v1.routers import (
    admin, audit, auth, connectors, health,
    kill_switch, metrics, policies, sessions, vbgw,
)

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
router.include_router(policies.router, prefix="/policies", tags=["policies"])
router.include_router(connectors.router, prefix="/connectors", tags=["connectors"])
router.include_router(kill_switch.router, prefix="/kill-switch", tags=["kill-switch"])
router.include_router(audit.router, prefix="/audit", tags=["audit"])
router.include_router(metrics.router, prefix="/metrics", tags=["metrics"])
router.include_router(admin.router, prefix="/admin", tags=["admin"])
# VBGW WebSocket — prefix 없이 등록 (라우터 내부에서 /ws/vbgw 경로 정의)
router.include_router(vbgw.router, tags=["vbgw"])
