"""Idempotency-Key Middleware — POST/PUT/PATCH/DELETE 중복 처리 방지.

동작:
  1. 비-mutating 메서드는 통과.
  2. WS / 헬스체크 / 문서 경로는 제외.
  3. settings.IDEMPOTENCY_REQUIRED_PATHS 의 prefix 에 매치되면 헤더 강제(미존재 → 400).
     그 외 경로는 헤더가 있을 때만 처리(opt-in).
  4. 같은 Idempotency-Key 로:
     - 처리 중(in_progress) → 409 + Retry-After
     - 완료(done) + 같은 body_hash → 저장된 응답 그대로 replay
     - 완료(done) + 다른 body_hash → 422 (헤더 재사용 위반)
  5. 핸들러 5xx → 슬롯 release. 클라이언트가 같은 key 로 재시도 가능.

성능 메모:
  - 정상(첫 요청) 경로는 Redis SET NX 1회 + (응답 후) SET 1회 만 추가됨.
  - 응답 본문은 Starlette StreamingResponse 도 안전하게 수집(iterator 1회 소비 후 재구성).
"""
from __future__ import annotations

import base64
import json
import re

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

from app.core.config import settings
from app.core.idempotency import (
    acquire_slot,
    build_idem_key,
    compute_body_hash,
    release_slot,
    store_response,
)

logger = structlog.get_logger(__name__)

_MUTATING_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})
_HEADER_NAME = "Idempotency-Key"
_EXCLUDED_PREFIXES = (
    "/api/v1/health",
    "/api/v1/metrics",
    "/api/docs",
    "/api/redoc",
    "/openapi.json",
)
_WS_PREFIX = "/api/v1/ws/"

# UUID v4 형태를 권장하지만 너무 빡빡하게 하면 클라이언트 호환성이 떨어진다.
# → 8~128자, 영숫자 + - _ 만 허용.
_KEY_SHAPE = re.compile(r"^[A-Za-z0-9\-_]{8,128}$")


def _required_prefixes() -> tuple[str, ...]:
    raw = (settings.IDEMPOTENCY_REQUIRED_PATHS or "").strip()
    if not raw:
        return ()
    return tuple(p.strip() for p in raw.split(",") if p.strip())


def _extract_tenant_id(request: Request) -> str | None:
    """JWT 페이로드에서 무검증으로 tenant_id 추출 (key 네임스페이스용)."""
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        return None
    token = auth[7:]
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        payload_b64 = parts[1]
        padding = (4 - len(payload_b64) % 4) % 4
        payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64).decode())
        return payload.get("tenant_id")
    except Exception:  # noqa: BLE001
        return None


def _is_excluded(path: str) -> bool:
    if path.startswith(_WS_PREFIX):
        return True
    return any(path.startswith(p) for p in _EXCLUDED_PREFIXES)


def _bad_request(code: str, message: str, status: int = 400) -> JSONResponse:
    return JSONResponse(
        status_code=status,
        content={"error": code, "message": message},
    )


async def _read_request_body(request: Request) -> bytes:
    """Starlette body() 는 캐시되므로 핸들러도 같은 바이트를 본다."""
    return await request.body()


async def _read_response_body(response: Response) -> tuple[bytes, Response]:
    """StreamingResponse 포함 모든 응답에서 바디를 수집.

    body_iterator 를 1회 소비하므로, 동일 body 로 새 Response 를 만들어 반환한다.
    """
    chunks: list[bytes] = []
    async for chunk in response.body_iterator:  # type: ignore[attr-defined]
        if isinstance(chunk, str):
            chunks.append(chunk.encode("utf-8"))
        else:
            chunks.append(chunk)
    body = b"".join(chunks)
    rebuilt = Response(
        content=body,
        status_code=response.status_code,
        headers=dict(response.headers),
        media_type=response.media_type,
    )
    return body, rebuilt


class IdempotencyMiddleware(BaseHTTPMiddleware):
    """Idempotency-Key 헤더 기반 mutating 요청 dedup 미들웨어."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:  # type: ignore[override]
        if not settings.IDEMPOTENCY_ENABLED:
            return await call_next(request)

        method = request.method.upper()
        if method not in _MUTATING_METHODS:
            return await call_next(request)

        path = request.url.path
        if _is_excluded(path):
            return await call_next(request)

        client_key = request.headers.get(_HEADER_NAME, "").strip()
        is_required = any(path.startswith(p) for p in _required_prefixes())

        if not client_key:
            if is_required:
                return _bad_request(
                    "IDEMPOTENCY_KEY_REQUIRED",
                    f"{_HEADER_NAME} 헤더가 필요합니다.",
                )
            return await call_next(request)

        if not _KEY_SHAPE.match(client_key):
            return _bad_request(
                "IDEMPOTENCY_KEY_INVALID",
                f"{_HEADER_NAME} 형식이 잘못됐습니다 (8~128자, 영숫자/-/_).",
            )

        tenant_id = _extract_tenant_id(request)
        body = await _read_request_body(request)
        body_hash = compute_body_hash(body)
        key = build_idem_key(
            tenant_id=tenant_id,
            method=method,
            path=path,
            client_key=client_key,
        )

        result = await acquire_slot(key=key, body_hash=body_hash)

        if not result.acquired:
            # 기존 레코드와 비교
            if result.existing_status == "in_progress":
                logger.info(
                    "idempotency_in_flight",
                    path=path,
                    tenant_id=tenant_id,
                    key_prefix=client_key[:8],
                )
                return JSONResponse(
                    status_code=409,
                    content={
                        "error": "IDEMPOTENCY_IN_PROGRESS",
                        "message": "동일 Idempotency-Key 로 진행 중인 요청이 있습니다.",
                    },
                    headers={"Retry-After": "5"},
                )
            if result.cached is not None:
                # body_hash 불일치 → 422
                if result.existing_body_hash and result.existing_body_hash != body_hash:
                    logger.warning(
                        "idempotency_key_reused_with_different_body",
                        path=path,
                        tenant_id=tenant_id,
                        key_prefix=client_key[:8],
                    )
                    return _bad_request(
                        "IDEMPOTENCY_KEY_MISMATCH",
                        "동일 Idempotency-Key 로 다른 요청 본문을 보냈습니다.",
                        status=422,
                    )
                # 정상 replay
                logger.info(
                    "idempotency_replay",
                    path=path,
                    tenant_id=tenant_id,
                    key_prefix=client_key[:8],
                    truncated=result.cached.truncated,
                )
                replay_headers = dict(result.cached.headers)
                replay_headers["Idempotent-Replay"] = "true"
                return Response(
                    content=result.cached.body,
                    status_code=result.cached.status_code,
                    headers=replay_headers,
                )
            # 손상/예외 — 일반 요청으로 통과 (fail-open)

        # 정상 처리 경로
        try:
            response = await call_next(request)
        except Exception:
            await release_slot(key=key)
            raise

        # 5xx 는 클라이언트 재시도 가능해야 하므로 슬롯 비움
        if response.status_code >= 500:
            await release_slot(key=key)
            return response

        body_bytes, rebuilt = await _read_response_body(response)
        await store_response(
            key=key,
            body_hash=body_hash,
            status_code=response.status_code,
            headers=dict(response.headers),
            body=body_bytes,
        )
        return rebuilt
