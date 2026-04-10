"""API v1 router aggregator."""
from fastapi import APIRouter

from app.api.v1.routers import auth, health, sessions

router = APIRouter()
router.include_router(health.router, tags=["health"])
router.include_router(auth.router, prefix="/auth", tags=["auth"])
router.include_router(sessions.router, prefix="/sessions", tags=["sessions"])
