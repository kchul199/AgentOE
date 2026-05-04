"""Rate Limit Middleware — Redis 토큰 버킷 기반 per-IP / per-tenant 분당 윈도우.

동작:
  - HTTP REST 경로에만 적용 (WS는 AdmissionControlMiddleware가 담당)
  - settings.RATE_LIMIT_ENABLED == False 또는 limit <= 0 이면 완전 우회
  - Redis 장애 시 fail-open (요청 허용). 재시도 폭주는 CB/Admission이 방어
  - 429 반환 시 Retry-After 헤더 포함

Key 스킴:
  agentoe:rl:ip:{ip}         분당 per-IP
  agentoe:rl:t:{tenant_id}   분당 per-tenant (JWT에서 추출)
"""
from __future__ import annotations

import base64
import json

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.redis_client import rate_limit_check

logger = structlog.get_logger(__name__)

# 적용 제외 경로 — healthcheck/metric은 부하테스트/프로브가 찔러대므로 제외
_EXCLUDED_PREFIXES = (
    "/api/v1/health",
    "/api/v1/metrics",
    "/api/docs",
    "/api/redoc",
    "/openapi.json",
)
_WS_PREFIX = "/api/v1/ws/"


def _extract_tenant_id(request: Request) -> str | None:
    """JWT Authorization 헤더 또는 WS query-param 'token' 에서 tenant_id 추출 (무검증)."""
    token: str | None = None
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        token = request.query_params.get("token")
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        return payload.get("tenant_id")
    except Exception:  # noqa: BLE001
        return None


def _client_ip(request: Request) -> str:
    # X-Forwarded-For (LB/Ingress)에서 첫 번째 IP를 우선 채택
    xff = request.headers.get("x-forwarded-for", "")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class RateLimitMiddleware(BaseHTTPMiddleware):
    """분당 per-IP / per-tenant rate limit. Redis 장애 시 fail-open."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if not settings.RATE_LIMIT_ENABLED:
            return await call_next(request)

        path = request.url.path
        # WS 업그레이드는 Admission에 위임, healthcheck 등은 제외
        if path.startswith(_WS_PREFIX) or any(path.startswith(p) for p in _EXCLUDED_PREFIXES):
            return await call_next(request)

        ip = _client_ip(request)
        ip_bucket = f"ip:{ip}"
        ip_ok = await rate_limit_check(ip_bucket, settings.RATE_LIMIT_PER_IP_PER_MIN, 60)
        if not ip_ok:
            logger.warning("rate_limit_exceeded", bucket="ip", ip=ip, path=path)
            return JSONResponse(
                status_code=429,
                content={
                    "error": "RATE_LIMIT_EXCEEDED",
                    "message": "요청이 너무 많습니다. 잠시 후 다시 시도해 주세요.",
                    "scope": "ip",
                },
                headers={"Retry-After": "30"},
            )

        tenant_id = _extract_tenant_id(request)
        if tenant_id:
            tenant_bucket = f"tenant:{tenant_id}"
            t_ok = await rate_limit_check(
                tenant_bucket, settings.RATE_LIMIT_PER_TENANT_PER_MIN, 60
            )
            if not t_ok:
                logger.warning(
                    "rate_limit_exceeded",
                    bucket="tenant",
                    tenant_id=tenant_id,
                    path=path,
                )
                return JSONResponse(
                    status_code=429,
                    content={
                        "error": "TENANT_RATE_LIMIT_EXCEEDED",
                        "message": "테넌트 분당 요청 한도를 초과했습니다.",
                        "scope": "tenant",
                    },
                    headers={"Retry-After": "30"},
                )

        return await call_next(request)
