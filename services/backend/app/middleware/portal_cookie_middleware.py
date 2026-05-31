"""Portal cookie → Authorization header 변환 미들웨어.

운영포탈 SPA 는 httponly `portal_access` 쿠키로 인증한다.
기존 `get_current_tenant` dependency 는 HTTPBearer (Authorization 헤더) 만 읽으므로,
이 미들웨어가 쿠키를 헤더로 승격시켜 기존 auth 파이프라인을 재사용한다.

적용 경로: /api/v1/admin/* 및 /api/v1/stream/*
  - 이미 Authorization 헤더가 있으면 스킵 (하위 호환).
  - portal_access 쿠키가 없으면 스킵 (HTTPBearer 가 401 처리).
"""

from __future__ import annotations

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

_PORTAL_COOKIE = "portal_access"
_PORTAL_PATHS = ("/api/v1/admin/", "/api/v1/stream/")


class PortalCookieMiddleware(BaseHTTPMiddleware):
    """portal_access 쿠키를 Authorization: Bearer 헤더로 승격."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        path = request.url.path

        if any(path.startswith(p) for p in _PORTAL_PATHS):  # noqa: SIM102
            # 이미 Authorization 헤더가 있으면 건드리지 않음
            if not request.headers.get("authorization"):
                token = request.cookies.get(_PORTAL_COOKIE)
                if token:
                    # Starlette request headers 는 불변 — scope 를 직접 수정
                    headers = dict(request.scope["headers"])
                    headers[b"authorization"] = f"Bearer {token}".encode()
                    request.scope["headers"] = list(headers.items())

        return await call_next(request)
