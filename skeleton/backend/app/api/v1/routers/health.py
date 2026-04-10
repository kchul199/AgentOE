"""Health check endpoint."""
from fastapi import APIRouter
from app.core.config import settings

router = APIRouter()


@router.get("/health")
async def health_check() -> dict:
    return {"status": "ok", "version": settings.VERSION, "env": settings.ENVIRONMENT}
