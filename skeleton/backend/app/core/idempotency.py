"""Idempotency 저장소 — Redis SETNX 기반.

설계 원칙:
  - Redis 단일 key 에 "in_progress" 레코드를 SETNX 로 심는다.
  - 원본 요청 처리 완료 후 동일 key 를 "done" 레코드로 덮어쓴다 (응답 포함).
  - 동일 key 로 재요청 시:
      * "in_progress" → 409 (처리 중)
      * "done"        → 저장된 응답 그대로 replay (idempotent 보장)
  - 바디 해시를 비교해 "같은 key 로 다른 바디" 공격/실수를 422 로 차단.
  - Redis 장애 시 fail-open — 미들웨어가 일반 요청처럼 흘려보낸다.
    (CLAUDE.md: "통화가 끊기지 않음이 최우선" — 쓰기 경로라도 Redis 부재로
    서비스 전체가 막히면 안 된다. 대신 메트릭/로그로 가시화.)

Key 스킴:
    agentoe:idem:{tenant}:{method}:{path_sha1}:{client_key}

레코드 JSON:
    {
      "v": 1,
      "status": "in_progress" | "done",
      "body_hash": <hex>,
      "response_status": int,              # done 만
      "response_headers": {name: value},   # done 만
      "response_body_b64": str,            # done, base64 (binary-safe)
      "response_truncated": bool,          # 바디가 LIMIT 초과 → True (body 비움)
      "created_at": float,
    }
"""
from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Any

import structlog

from app.core.config import settings
from app.core.redis_client import get_redis, scoped_key

try:
    from redis.exceptions import RedisError
except ImportError:  # 테스트 환경 graceful fallback — redis 미설치
    RedisError = Exception  # type: ignore

logger = structlog.get_logger(__name__)

_RECORD_VERSION = 1
_SCHEMA_STATUS_IN_PROGRESS = "in_progress"
_SCHEMA_STATUS_DONE = "done"

# 응답 replay 시 제외하는 헤더 — 매 요청마다 새로 붙어야 정상.
# (Content-Length 는 응답 바디에서 재계산)
_REPLAY_EXCLUDED_HEADERS = frozenset({
    "content-length",
    "date",
    "server",
    "x-request-id",
    "x-trace-id",
    "connection",
    "transfer-encoding",
})


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CachedResponse:
    """저장된 응답을 담는 read-only 레코드."""

    status_code: int
    headers: dict[str, str]
    body: bytes
    truncated: bool


@dataclass(frozen=True)
class AcquireResult:
    """SETNX 시도 결과."""

    acquired: bool                     # 새로 slot 을 잡았는지
    existing_status: str | None        # 기존 레코드 상태 (in_progress|done|None)
    existing_body_hash: str | None     # 기존 레코드의 요청 바디 해시
    cached: CachedResponse | None      # existing_status == done 일 때만


# ── 키 빌더 / 바디 해시 ───────────────────────────────────────────────────────


def build_idem_key(
    *,
    tenant_id: str | None,
    method: str,
    path: str,
    client_key: str,
) -> str:
    """사용자 제공 key 를 우리 네임스페이스에 정규화해 담는다.

    path 를 그대로 쓰면 긴 path 나 path param 이 포함돼 카디널리티가 커진다.
    → sha1(path) 의 첫 16 hex 로 축약. 같은 path 는 같은 해시를 갖는다.
    """
    path_hash = hashlib.sha1(path.encode("utf-8")).hexdigest()[:16]  # noqa: S324
    return scoped_key(
        "idem",
        method.upper(),
        path_hash,
        client_key,
        tenant_id=tenant_id,
    )


def compute_body_hash(body: bytes) -> str:
    """바디 전체에 대한 sha256 hex. 빈 바디도 안정적으로 다룬다."""
    return hashlib.sha256(body or b"").hexdigest()


# ── Redis I/O ─────────────────────────────────────────────────────────────────


def _serialize_in_progress(body_hash: str) -> str:
    return json.dumps({
        "v": _RECORD_VERSION,
        "status": _SCHEMA_STATUS_IN_PROGRESS,
        "body_hash": body_hash,
        "created_at": time.time(),
    })


def _serialize_done(
    *,
    body_hash: str,
    status_code: int,
    headers: dict[str, str],
    body: bytes,
) -> str:
    limit = settings.IDEMPOTENCY_MAX_BODY_BYTES
    truncated = len(body) > limit
    payload_body = b"" if truncated else body
    return json.dumps({
        "v": _RECORD_VERSION,
        "status": _SCHEMA_STATUS_DONE,
        "body_hash": body_hash,
        "response_status": int(status_code),
        "response_headers": _filter_replay_headers(headers),
        "response_body_b64": base64.b64encode(payload_body).decode("ascii"),
        "response_truncated": truncated,
        "created_at": time.time(),
    })


def _filter_replay_headers(h: dict[str, str]) -> dict[str, str]:
    return {
        k: v
        for k, v in h.items()
        if k.lower() not in _REPLAY_EXCLUDED_HEADERS
    }


def _deserialize(raw: str) -> dict[str, Any] | None:
    try:
        obj = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None
    if not isinstance(obj, dict):
        return None
    if obj.get("v") != _RECORD_VERSION:
        return None
    return obj


def _record_to_cached(obj: dict[str, Any]) -> CachedResponse | None:
    if obj.get("status") != _SCHEMA_STATUS_DONE:
        return None
    body_b64 = obj.get("response_body_b64", "")
    try:
        body = base64.b64decode(body_b64)
    except Exception:  # noqa: BLE001
        body = b""
    headers = obj.get("response_headers", {}) or {}
    return CachedResponse(
        status_code=int(obj.get("response_status", 200)),
        headers={str(k): str(v) for k, v in headers.items()},
        body=body,
        truncated=bool(obj.get("response_truncated", False)),
    )


async def acquire_slot(*, key: str, body_hash: str) -> AcquireResult:
    """SETNX 로 "in_progress" 슬롯을 선점.

    - 새로 잡으면 acquired=True.
    - 이미 있으면 기존 값을 읽어 acquired=False + 상태/바디해시/캐시 응답을 반환.
    - Redis 장애면 acquired=True + existing_status=None 반환해 fail-open 유도.
    """
    payload = _serialize_in_progress(body_hash)
    try:
        r = get_redis()
        # Redis 6.2+ 는 SET NX EX XX GET 을 지원 → 조회+선점 원자화
        result = await r.set(
            key,
            payload,
            nx=True,
            ex=settings.IDEMPOTENCY_TTL_SECONDS,
        )
        if result is True:
            return AcquireResult(
                acquired=True,
                existing_status=None,
                existing_body_hash=None,
                cached=None,
            )
        # 이미 존재 — 기존 값을 읽는다
        existing_raw = await r.get(key)
        if not existing_raw:
            # race: TTL 만료되어 사라진 순간. 다시 잡아준다.
            retry = await r.set(
                key,
                payload,
                nx=True,
                ex=settings.IDEMPOTENCY_TTL_SECONDS,
            )
            if retry is True:
                return AcquireResult(
                    acquired=True,
                    existing_status=None,
                    existing_body_hash=None,
                    cached=None,
                )
            return AcquireResult(
                acquired=False,
                existing_status=_SCHEMA_STATUS_IN_PROGRESS,
                existing_body_hash=None,
                cached=None,
            )
        obj = _deserialize(existing_raw)
        if obj is None:
            # 손상된 레코드 — 안전하게 덮어쓰고 새로 잡는다
            await r.set(key, payload, ex=settings.IDEMPOTENCY_TTL_SECONDS)
            return AcquireResult(
                acquired=True,
                existing_status=None,
                existing_body_hash=None,
                cached=None,
            )
        return AcquireResult(
            acquired=False,
            existing_status=obj.get("status"),
            existing_body_hash=obj.get("body_hash"),
            cached=_record_to_cached(obj),
        )
    except RedisError as e:
        logger.warning("idempotency_acquire_failed", key=key, error=str(e))
        # fail-open: 미들웨어가 일반 요청처럼 진행하도록 acquired=True 리턴
        return AcquireResult(
            acquired=True,
            existing_status=None,
            existing_body_hash=None,
            cached=None,
        )


async def store_response(
    *,
    key: str,
    body_hash: str,
    status_code: int,
    headers: dict[str, str],
    body: bytes,
) -> None:
    """요청 처리 완료 후 "done" 레코드로 덮어쓴다."""
    try:
        payload = _serialize_done(
            body_hash=body_hash,
            status_code=status_code,
            headers=headers,
            body=body,
        )
        await get_redis().set(key, payload, ex=settings.IDEMPOTENCY_TTL_SECONDS)
    except RedisError as e:
        logger.warning("idempotency_store_failed", key=key, error=str(e))


async def release_slot(*, key: str) -> None:
    """5xx 등으로 핸들러가 실패했을 때 "in_progress" 레코드를 제거.

    그대로 두면 TTL 동안 재시도 클라이언트가 409 로 막힘 → 실패한 건에 대한
    재시도는 원래 통하는 정상 시나리오인데 idempotency 가 방해하면 안 됨.
    """
    try:
        await get_redis().delete(key)
    except RedisError as e:
        logger.warning("idempotency_release_failed", key=key, error=str(e))
