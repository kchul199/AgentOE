"""Redis async client for session hot state and lease locks."""
import json
import logging
from typing import Any

try:
    import redis.asyncio as aioredis
except ImportError:  # 테스트 환경에서 redis 미설치 시 graceful fallback
    aioredis = None  # type: ignore

from app.core.config import settings

logger = logging.getLogger(__name__)

_redis: aioredis.Redis | None = None


async def init_redis() -> None:
    """Initialize Redis connection pool."""
    global _redis
    _redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=50,
    )
    await _redis.ping()
    logger.info("Redis connected", url=settings.REDIS_URL)


async def close_redis() -> None:
    global _redis
    if _redis:
        await _redis.aclose()
        _redis = None


def get_redis() -> aioredis.Redis:
    if _redis is None:
        raise RuntimeError("Redis not initialized. Call init_redis() first.")
    return _redis


# ── Session Hot State ────────────────────────────────────────────────────────

SESSION_KEY = "session:{session_id}"
SESSION_TTL = 3600  # 1시간


async def set_session_state(session_id: str, state: dict[str, Any]) -> None:
    """Save session state to Redis (hot path)."""
    key = SESSION_KEY.format(session_id=session_id)
    await get_redis().setex(key, SESSION_TTL, json.dumps(state, default=str))


async def get_session_state(session_id: str) -> dict[str, Any] | None:
    """Get session state from Redis. Returns None if not found."""
    key = SESSION_KEY.format(session_id=session_id)
    raw = await get_redis().get(key)
    return json.loads(raw) if raw else None


async def delete_session_state(session_id: str) -> None:
    key = SESSION_KEY.format(session_id=session_id)
    await get_redis().delete(key)


# ── Lease Lock (중복 처리 방지) ───────────────────────────────────────────────

LEASE_KEY = "lease:{session_id}"
LEASE_TTL = 30  # 30초


async def acquire_lease(session_id: str) -> bool:
    """Try to acquire processing lease. Returns True if acquired."""
    key = LEASE_KEY.format(session_id=session_id)
    result = await get_redis().set(key, "1", ex=LEASE_TTL, nx=True)
    return result is True


async def release_lease(session_id: str) -> None:
    key = LEASE_KEY.format(session_id=session_id)
    await get_redis().delete(key)


# ── Kill Switch Cache (빠른 조회) ─────────────────────────────────────────────

KILL_SWITCH_KEY = "kill_switch:{scope}:{target_id}"
KILL_SWITCH_TTL = 60  # 1분 캐시


async def cache_kill_switch(scope: str, target_id: str, active: bool) -> None:
    key = KILL_SWITCH_KEY.format(scope=scope, target_id=target_id)
    await get_redis().setex(key, KILL_SWITCH_TTL, "1" if active else "0")


async def get_kill_switch_cached(scope: str, target_id: str) -> bool | None:
    """Returns True/False if cached, None if cache miss."""
    key = KILL_SWITCH_KEY.format(scope=scope, target_id=target_id)
    val = await get_redis().get(key)
    if val is None:
        return None
    return val == "1"
