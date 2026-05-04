"""
Admission Control Middleware — 테넌트별 동시 WebSocket 세션 한도 적용.

동작 방식:
  - WebSocket 업그레이드 요청(/api/v1/ws/) 감지 시, Redis에서 테넌트 활성 세션 수 확인
  - MAX_SESSIONS_PER_TENANT 초과 시 HTTP 429 반환 (WebSocket 업그레이드 거부)
  - 세션 연결 성공: Redis 카운터 증가 (key: admission:{tenant_id})
  - 세션 종료:    Redis 카운터 감소 — vbgw.py finally 블록에서 호출

Redis 키:
  admission:{tenant_id}  →  현재 활성 세션 수 (INTEGER)
  admission:{tenant_id}:ttl_guard  →  카운터 누수 방지용 TTL 키 (24시간)
"""
from __future__ import annotations

import base64
import json
import logging

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

# WebSocket 경로 prefix (admission 적용 대상)
_WS_PATH_PREFIX = "/api/v1/ws/"

# Redis 키 패턴
_ADMISSION_KEY = "admission:{tenant_id}"
_GUARD_KEY = "admission:{tenant_id}:ttl_guard"
_GUARD_TTL = 86400  # 24시간 — 프로세스 crash 시 카운터 자동 정리


def _extract_tenant_id(request: Request) -> str | None:
    """
    JWT Authorization 헤더 또는 WebSocket query-param 'token' 에서
    tenant_id 추출 (서명 검증 없이 페이로드만 파싱).
    """
    token: str | None = None

    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        token = auth[7:]
    else:
        # WebSocket 업그레이드는 Authorization 헤더가 없고 query param 사용
        token = request.query_params.get("token")

    if not token:
        return None

    parts = token.split(".")
    if len(parts) != 3:
        return None

    try:
        payload_b64 = parts[1]
        # base64url padding 보완
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        return payload.get("tenant_id")
    except Exception:  # noqa: BLE001
        return None


async def get_tenant_session_count(tenant_id: str) -> int:
    """현재 테넌트의 활성 세션 수를 Redis에서 조회."""
    from app.core.redis_client import get_redis

    try:
        val = await get_redis().get(_ADMISSION_KEY.format(tenant_id=tenant_id))
        return int(val) if val else 0
    except Exception as exc:  # noqa: BLE001
        logger.warning("admission count read failed for %s: %s", tenant_id, exc)
        return 0  # Redis 장애 시 fail-open (세션 허용)


async def increment_session_count(tenant_id: str) -> int:
    """
    테넌트 활성 세션 수를 +1하고 새 값을 반환.
    TTL guard 키로 카운터 누수를 방지한다.
    """
    from app.core.redis_client import get_redis

    redis = get_redis()
    key = _ADMISSION_KEY.format(tenant_id=tenant_id)
    guard_key = _GUARD_KEY.format(tenant_id=tenant_id)
    try:
        count = await redis.incr(key)
        # 처음 키가 생성될 때(count==1)마다 TTL guard 갱신
        await redis.set(guard_key, "1", ex=_GUARD_TTL)
        return count
    except Exception as exc:  # noqa: BLE001
        logger.warning("admission increment failed for %s: %s", tenant_id, exc)
        return 1


async def decrement_session_count(tenant_id: str) -> None:
    """
    테넌트 활성 세션 수를 -1한다.
    0 미만으로 내려가지 않도록 Lua 스크립트로 atomic 처리.
    """
    from app.core.redis_client import get_redis

    key = _ADMISSION_KEY.format(tenant_id=tenant_id)
    # DECR 후 음수가 되면 0으로 클램프
    _LUA_DEC_FLOOR = """
local v = redis.call('DECR', KEYS[1])
if v < 0 then
    redis.call('SET', KEYS[1], 0)
end
return v
"""
    try:
        await get_redis().eval(_LUA_DEC_FLOOR, 1, key)
    except Exception as exc:  # noqa: BLE001
        logger.warning("admission decrement failed for %s: %s", tenant_id, exc)


class AdmissionControlMiddleware(BaseHTTPMiddleware):
    """
    WebSocket 업그레이드 요청에 대해 테넌트별 동시 세션 한도를 적용.

    - WS 경로가 아니면 통과 (HTTP REST API에는 적용 안 함)
    - Redis 장애 시 fail-open (세션 허용) — 가용성 우선
    - 한도 초과: HTTP 429 + Retry-After: 5 헤더
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)
        # 설정은 lazy import (startup 전에 미들웨어 인스턴스화 발생)
        self._limit: int | None = None

    def _get_limit(self) -> int:
        if self._limit is None:
            try:
                from app.core.config import settings
                self._limit = settings.MAX_SESSIONS_PER_TENANT
            except Exception:  # noqa: BLE001
                self._limit = 100
        return self._limit

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        # WS 경로가 아니면 즉시 통과
        if not request.url.path.startswith(_WS_PATH_PREFIX):
            return await call_next(request)

        # WebSocket 업그레이드 요청인지 확인
        upgrade = request.headers.get("upgrade", "").lower()
        if upgrade != "websocket":
            return await call_next(request)

        tenant_id = _extract_tenant_id(request)
        if not tenant_id:
            # 토큰 없으면 vbgw 핸들러가 4001로 처리
            return await call_next(request)

        current = await get_tenant_session_count(tenant_id)
        limit = self._get_limit()

        if current >= limit:
            logger.warning(
                "Admission denied: tenant=%s current=%d limit=%d",
                tenant_id, current, limit,
            )
            return JSONResponse(
                status_code=429,
                content={
                    "error": "SESSION_LIMIT_EXCEEDED",
                    "message": (
                        f"최대 동시 세션 수({limit})를 초과했습니다. "
                        "잠시 후 다시 시도해 주세요."
                    ),
                    "current_sessions": current,
                    "max_sessions": limit,
                },
                headers={"Retry-After": "5"},
            )

        return await call_next(request)
