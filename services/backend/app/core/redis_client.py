"""Redis async client for session hot state and lease locks.

설계 원칙:
  - 모든 키는 namespace + tenant 스코프로 감싸 크로스테넌트 leak 방지
  - Redis 일시 장애는 예외 삼키고 None 반환(Graceful Degradation) —
    호출부가 Mongo fallback 또는 세션 재시작을 선택할 수 있게 함
  - Hot path에 불필요한 await 추가 금지 (Latency is King)
"""

import json
from typing import Any

try:
    import redis.asyncio as aioredis
    from redis.exceptions import RedisError
except ImportError:  # 테스트 환경에서 redis 미설치 시 graceful fallback
    aioredis = None  # type: ignore
    RedisError = Exception  # type: ignore

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)

_redis: "aioredis.Redis | None" = None


# ── Key namespacing (멀티테넌시 격리) ────────────────────────────────────────
#
# scoped_key("session", session_id, tenant_id=tid)
#   → "agentoe:t:tid:session:sid"   (REDIS_TENANT_SCOPED_KEYS=True)
#   → "agentoe:session:sid"         (False, 레거시 호환)
#
# scoped_key("kill_switch:feature", target_id)
#   → "agentoe:kill_switch:feature:tid"  (tenant scope 없음: 글로벌 키)


def scoped_key(kind: str, *parts: str, tenant_id: str | None = None) -> str:
    """Namespaced Redis key builder. tenant 스코프 강제 시 prefix 자동 삽입."""
    pieces: list[str] = [settings.REDIS_KEY_NAMESPACE]
    if tenant_id and settings.REDIS_TENANT_SCOPED_KEYS:
        pieces.extend(["t", tenant_id])
    pieces.append(kind)
    pieces.extend(parts)
    return ":".join(pieces)


async def init_redis() -> None:
    """Initialize Redis connection pool."""
    global _redis
    _redis = aioredis.from_url(
        settings.REDIS_URL,
        encoding="utf-8",
        decode_responses=True,
        max_connections=settings.REDIS_POOL_SIZE,
        socket_connect_timeout=settings.HTTP_CONNECT_TIMEOUT,
        socket_timeout=settings.HTTP_READ_TIMEOUT,
        health_check_interval=30,
        retry_on_timeout=True,
    )
    await _redis.ping()  # type: ignore[misc]
    logger.info("Redis connected", pool_size=settings.REDIS_POOL_SIZE)


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
#
# SLA: Redis 일시 장애 시 상위 전파 없이 None 반환 — ai_pipeline이 Mongo fallback.

SESSION_TTL = 3600  # 1시간


async def set_session_state(
    session_id: str, state: dict[str, Any], tenant_id: str | None = None
) -> bool:
    """Save session state. Redis 장애 시 False 반환(로그만 남김)."""
    try:
        key = scoped_key("session", session_id, tenant_id=tenant_id)
        await get_redis().setex(key, SESSION_TTL, json.dumps(state, default=str))
        return True
    except RedisError as e:
        logger.warning("redis_set_session_state_failed", session_id=session_id, error=str(e))
        return False


async def get_session_state(session_id: str, tenant_id: str | None = None) -> dict[str, Any] | None:
    """Redis 미스/장애 모두 None. 호출부가 Mongo fallback 여부 결정."""
    try:
        key = scoped_key("session", session_id, tenant_id=tenant_id)
        raw = await get_redis().get(key)
        return json.loads(raw) if raw else None
    except RedisError as e:
        logger.warning("redis_get_session_state_failed", session_id=session_id, error=str(e))
        return None


async def delete_session_state(session_id: str, tenant_id: str | None = None) -> None:
    try:
        key = scoped_key("session", session_id, tenant_id=tenant_id)
        await get_redis().delete(key)
    except RedisError as e:
        logger.warning("redis_delete_session_state_failed", session_id=session_id, error=str(e))


# ── Lease Lock (중복 처리 방지) ───────────────────────────────────────────────

LEASE_TTL = 30  # 30초


async def acquire_lease(session_id: str, tenant_id: str | None = None) -> bool:
    """Try to acquire processing lease. Redis 장애 시 False (안전한 실패)."""
    try:
        key = scoped_key("lease", session_id, tenant_id=tenant_id)
        result = await get_redis().set(key, "1", ex=LEASE_TTL, nx=True)
        return result is True
    except RedisError as e:
        logger.warning("redis_acquire_lease_failed", session_id=session_id, error=str(e))
        return False


async def release_lease(session_id: str, tenant_id: str | None = None) -> None:
    try:
        key = scoped_key("lease", session_id, tenant_id=tenant_id)
        await get_redis().delete(key)
    except RedisError as e:
        logger.warning("redis_release_lease_failed", session_id=session_id, error=str(e))


# ── Kill Switch Cache (빠른 조회) ─────────────────────────────────────────────

KILL_SWITCH_TTL = 60  # 1분 캐시


async def cache_kill_switch(scope: str, target_id: str, active: bool) -> None:
    try:
        key = scoped_key("kill_switch", scope, target_id)
        await get_redis().setex(key, KILL_SWITCH_TTL, "1" if active else "0")
    except RedisError as e:
        logger.warning(
            "redis_kill_switch_cache_failed", scope=scope, target_id=target_id, error=str(e)
        )


async def get_kill_switch_cached(scope: str, target_id: str) -> bool | None:
    """Returns True/False if cached, None if cache miss OR Redis down."""
    try:
        key = scoped_key("kill_switch", scope, target_id)
        val = await get_redis().get(key)
        if val is None:
            return None
        return val == "1"
    except RedisError as e:
        logger.warning(
            "redis_kill_switch_get_failed", scope=scope, target_id=target_id, error=str(e)
        )
        return None


# ── Dead Letter Queue (실패 턴 보관) ──────────────────────────────────────────
#
# 용도: STT/LLM/TTS 파이프라인 실패, Circuit Breaker OPEN 중 drop 등
# 원본 turn context를 Redis Stream에 쌓아 14일 보존 → 재처리/사후분석.
# Consumer는 별도 worker(app/jobs/dlq_worker.py, 스켈레톤 범위 외)가 소비.

DLQ_STREAM = "agentoe:dlq:failed_turns"
DLQ_MAXLEN = 50_000  # 약 14일 * 100 CCU * 평균 5턴/분 /스트림 안전마진


async def enqueue_failed_turn(payload: dict[str, Any]) -> str | None:
    """
    실패한 턴을 DLQ로 보냄. 성공 시 Stream ID, 실패 시 None.
    반드시 non-blocking: Redis 장애가 상위 요청을 막아서는 안 됨.
    """
    try:
        serialized = {
            k: json.dumps(v, default=str) if not isinstance(v, str) else v
            for k, v in payload.items()
        }
        sid = await get_redis().xadd(DLQ_STREAM, serialized, maxlen=DLQ_MAXLEN, approximate=True)  # type: ignore[arg-type]
        return sid
    except RedisError as e:
        logger.error("dlq_enqueue_failed", error=str(e), payload_keys=list(payload.keys()))
        return None


# ── Rate Limit (토큰 버킷 Lua) ───────────────────────────────────────────────
#
# middleware/rate_limit_middleware.py가 사용. 분당 윈도우.

_RATE_LIMIT_LUA = """
local key = KEYS[1]
local limit = tonumber(ARGV[1])
local window = tonumber(ARGV[2])
local current = redis.call('INCR', key)
if current == 1 then
    redis.call('EXPIRE', key, window)
end
if current > limit then
    return 0
end
return 1
"""


async def rate_limit_check(bucket: str, limit: int, window_seconds: int = 60) -> bool:
    """
    True = 허용, False = 초과. Redis 장애 시 True(failing open) —
    요청을 막지 않음. 재시도 폭주는 이후 CB/AdmissionControl이 처리.
    """
    if not settings.RATE_LIMIT_ENABLED or limit <= 0:
        return True
    try:
        key = scoped_key("rl", bucket)
        result = await get_redis().eval(_RATE_LIMIT_LUA, 1, key, limit, window_seconds)  # type: ignore[misc]
        return result == 1
    except RedisError as e:
        logger.warning("rate_limit_check_failed", bucket=bucket, error=str(e))
        return True  # fail open
