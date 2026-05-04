"""
Logging Middleware — 요청별 context var 자동 주입 + 접근 로그

기능:
  1. 모든 HTTP 요청에 request_id 자동 생성 (X-Request-ID 헤더 우선)
  2. JWT에서 tenant_id 추출 → structlog context var 바인딩
  3. 응답 후 요청 완료 로그 (경로, 상태코드, 소요시간)
  4. 요청 종료 시 context var 정리 (다음 요청 오염 방지)
  5. WebSocket(/ws/*) 경로는 접근 로그 제외 (vbgw에서 별도 처리)

성능:
  - JWT 디코딩: 서명 검증 없이 페이로드만 추출 (인증은 auth.py에서 수행)
  - Redis/DB 조회 없음 — 순수 in-memory 처리
"""
from __future__ import annotations

import base64
import json
import logging
import time
import uuid

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

from app.core.logging import (
    bind_request_context,
    clear_request_context,
)

logger = structlog.get_logger(__name__)

# 접근 로그 제외 경로 (성능 민감 경로)
_SKIP_ACCESS_LOG = frozenset(["/api/v1/health", "/metrics"])
# WebSocket 경로 prefix
_WS_PREFIX = "/api/v1/ws/"


def _extract_tenant_from_jwt(authorization: str | None) -> tuple[str | None, str | None]:
    """
    Authorization 헤더에서 JWT payload 추출 (서명 검증 없음).
    반환: (tenant_id, client_id) or (None, None)
    """
    if not authorization or not authorization.startswith("Bearer "):
        return None, None
    token = authorization[7:]
    try:
        parts = token.split(".")
        if len(parts) != 3:
            return None, None
        # Base64URL → JSON 디코딩
        payload_b64 = parts[1] + "=="  # padding 보정
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return payload.get("tenant_id"), payload.get("sub")
    except Exception:  # noqa: BLE001
        return None, None


class LoggingMiddleware(BaseHTTPMiddleware):
    """
    요청별 structlog context var 자동 바인딩 미들웨어.

    처리 순서:
      1. request_id 결정 (X-Request-ID 헤더 → 신규 생성)
      2. tenant_id/client_id JWT에서 추출
      3. context var 바인딩
      4. 요청 처리 (next middleware/handler)
      5. 접근 로그 기록
      6. context var 정리
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        # WebSocket 업그레이드 요청은 통과 (WS 자체에서 처리)
        if request.url.path.startswith(_WS_PREFIX):
            return await call_next(request)

        start_time = time.monotonic()

        # 1. Request ID
        request_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())[:8]

        # 2. Tenant / Client ID
        tenant_id, client_id = _extract_tenant_from_jwt(
            request.headers.get("Authorization")
        )

        # 3. Context var 바인딩
        bind_request_context(
            request_id=request_id,
            tenant_id=tenant_id,
            client_id=client_id,
            path=request.url.path,
            method=request.method,
        )

        # 4~5. 요청 처리 + 접근 로그를 모두 try 안에서 수행해
        #       bound context (request_id / tenant_id) 가 access log 에 포함되도록
        #       보장한다. clear 는 예외 경로에서도 반드시 호출되도록 finally 에 유지.
        response: Response | None = None
        try:
            try:
                response = await call_next(request)
            except Exception as exc:
                elapsed_ms = (time.monotonic() - start_time) * 1000
                logger.error(
                    "Request failed with exception",
                    status_code=500,
                    elapsed_ms=round(elapsed_ms, 2),
                    exc_info=exc,
                )
                raise

            # 5. 접근 로그 (bound context 가 아직 살아있는 상태에서 기록)
            elapsed_ms = (time.monotonic() - start_time) * 1000
            if request.url.path not in _SKIP_ACCESS_LOG:
                log_fn = logger.warning if response.status_code >= 400 else logger.info
                log_fn(
                    "HTTP request completed",
                    status_code=response.status_code,
                    elapsed_ms=round(elapsed_ms, 2),
                )
        finally:
            # 6. context var 정리 — 태스크 재사용/후속 요청에 오염되지 않도록
            #    반드시 전부 비운다 (예외 경로 포함).
            clear_request_context()

        # 7. 응답 헤더에 request_id 추가 (디버깅 편의)
        assert response is not None  # 예외 시 이미 raise 됨
        response.headers["X-Request-ID"] = request_id
        return response
