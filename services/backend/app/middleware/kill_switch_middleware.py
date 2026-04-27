"""Kill Switch middleware — blocks requests when active."""
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.redis_client import get_kill_switch_cached
from app.domain.kill_switch import KillSwitchScope

# Kill Switch 체크를 건너뛸 경로
EXEMPT_PATHS = {
    "/api/v1/health",
    "/api/v1/admin/kill-switch",
    "/api/docs",
    "/api/redoc",
    "/openapi.json",
}


class KillSwitchMiddleware(BaseHTTPMiddleware):
    """
    각 요청마다 Redis에서 테넌트/기능 레벨 Kill Switch 확인.
    캐시 히트 시 ~0.1ms 오버헤드.
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # 예외 경로 스킵
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # 테넌트 컨텍스트 추출 (JWT 파싱 전이므로 헤더에서)
        tenant_id = request.headers.get("X-Tenant-ID")
        if tenant_id:
            active = await get_kill_switch_cached(
                KillSwitchScope.TENANT.value, tenant_id
            )
            # 캐시 미스 → 미들웨어에서는 패스 (라우터 레벨에서 DB 조회)
            if active is True:
                return JSONResponse(
                    status_code=503,
                    content={
                        "error": "KILL_SWITCH_ACTIVE",
                        "message": f"테넌트 {tenant_id} 서비스가 일시 중지되었습니다.",
                    },
                )

        return await call_next(request)
