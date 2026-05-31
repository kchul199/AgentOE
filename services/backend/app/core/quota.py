"""
LLM Token / Cost Quota — 테넌트 일일 한도 (Redis 카운터 + Graceful Degradation).

사용처:
  - llm_service.py 의 complete/stream 시작점에서 check_and_reserve() 호출
  - 응답 완료 후 commit_usage() 로 실제 사용량을 반영

모델:
  키 스킴 (일 단위 롤업):
    agentoe:t:{tid}:quota:llm:tokens:{YYYYMMDD}   (INCRBY)
    agentoe:t:{tid}:quota:llm:cost_cents:{YYYYMMDD}

정책(초과 시 동작):
  - "fallback": QuotaExceededError(graceful=True) → 시나리오의 fallback 노드로 분기
  - "reject"  : QuotaExceededError(graceful=False) → 즉시 호출 거부 (HTTP 429)
  - "warn"    : 로그만 남기고 통과 (관측 기간)

CLAUDE.md 원칙:
  - Redis 장애 시 fail-open (통과) — 통화가 끊기지 않음이 최우선
  - 카운터는 atomic INCRBY, 만료(EXPIRE) 로 자연 롤업
  - 비동기 I/O, latency 영향 최소 (1 라운드트립)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import structlog

from app.core.config import settings
from app.core.metrics import record_quota_check
from app.core.redis_client import get_redis, scoped_key

try:
    from redis.exceptions import RedisError
except ImportError:  # pragma: no cover
    RedisError = Exception  # type: ignore

logger = structlog.get_logger(__name__)


class QuotaExceededError(Exception):
    """쿼터 초과 — graceful=True 면 시나리오 fallback 으로 분기 허용."""

    def __init__(self, message: str, *, graceful: bool, scope: str) -> None:
        super().__init__(message)
        self.graceful = graceful
        self.scope = scope  # "tokens" | "cost"


@dataclass(frozen=True)
class QuotaStatus:
    tokens_used_today: int
    tokens_limit: int
    cost_cents_used_today: int
    cost_cents_limit: int
    # True 면 이미 초과 상태
    over_tokens: bool
    over_cost: bool

    @property
    def over(self) -> bool:
        return self.over_tokens or self.over_cost


def _today_utc() -> str:
    return datetime.now(UTC).strftime("%Y%m%d")


def _tokens_key(tenant_id: str) -> str:
    return scoped_key("quota:llm:tokens", _today_utc(), tenant_id=tenant_id)


def _cost_key(tenant_id: str) -> str:
    return scoped_key("quota:llm:cost_cents", _today_utc(), tenant_id=tenant_id)


def _limits_for(tenant_cfg: dict | None) -> tuple[int, int]:
    """(tokens_limit, cost_cents_limit). 0 == 무제한."""
    token_limit = settings.LLM_DAILY_TOKEN_QUOTA_DEFAULT
    cost_limit = settings.LLM_DAILY_COST_QUOTA_CENTS_DEFAULT
    if tenant_cfg:
        token_limit = int(tenant_cfg.get("llm_daily_token_quota", token_limit))
        cost_limit = int(tenant_cfg.get("llm_daily_cost_quota_cents", cost_limit))
    return token_limit, cost_limit


async def check_quota(tenant_id: str, tenant_cfg: dict | None = None) -> QuotaStatus:
    """
    호출 시작점에서 쿼터를 확인만 한다 (증가 X). Redis 장애 시 fail-open (0/0 반환).
    """
    token_limit, cost_limit = _limits_for(tenant_cfg)

    tokens_used = 0
    cost_used = 0
    try:
        r = get_redis()
        # MGET 한 번으로 2개 값 조회 — 1 RTT
        values = await r.mget([_tokens_key(tenant_id), _cost_key(tenant_id)])
        tokens_used = int(values[0] or 0)
        cost_used = int(values[1] or 0)
    except RedisError as e:
        logger.warning("quota.check_failed_fail_open", tenant_id=tenant_id, error=str(e))

    return QuotaStatus(
        tokens_used_today=tokens_used,
        tokens_limit=token_limit,
        cost_cents_used_today=cost_used,
        cost_cents_limit=cost_limit,
        over_tokens=(token_limit > 0 and tokens_used >= token_limit),
        over_cost=(cost_limit > 0 and cost_used >= cost_limit),
    )


async def enforce_quota(
    tenant_id: str,
    tenant_cfg: dict | None = None,
) -> QuotaStatus:
    """
    쿼터 초과 시 policy 에 따라 QuotaExceededError 발생.
    정책: settings.LLM_QUOTA_EXCEEDED_BEHAVIOR = "fallback" | "reject" | "warn"
    """
    if not settings.LLM_QUOTA_ENABLED:
        # 비활성 상태는 관측하지 않음 (레이블 폭발 방지)
        return QuotaStatus(0, 0, 0, 0, False, False)

    status = await check_quota(tenant_id, tenant_cfg)
    if not status.over:
        record_quota_check(tenant_id, scope="none", result="ok")
        return status

    scope = "tokens" if status.over_tokens else "cost"
    policy = settings.LLM_QUOTA_EXCEEDED_BEHAVIOR

    if policy == "warn":
        logger.warning(
            "quota.exceeded_warn_only",
            tenant_id=tenant_id,
            scope=scope,
            tokens=status.tokens_used_today,
            cost=status.cost_cents_used_today,
        )
        record_quota_check(tenant_id, scope=scope, result="warn")
        return status

    graceful = policy == "fallback"
    logger.warning(
        "quota.exceeded",
        tenant_id=tenant_id,
        scope=scope,
        policy=policy,
        tokens_used=status.tokens_used_today,
        tokens_limit=status.tokens_limit,
        cost_used=status.cost_cents_used_today,
        cost_limit=status.cost_cents_limit,
    )
    record_quota_check(
        tenant_id,
        scope=scope,
        result="fallback" if graceful else "reject",
    )
    raise QuotaExceededError(
        f"Tenant daily LLM {scope} quota exceeded",
        graceful=graceful,
        scope=scope,
    )


async def commit_usage(
    tenant_id: str,
    *,
    tokens: int,
    cost_cents: float = 0.0,
    ttl_seconds: int = 172800,  # 48h — 하루치 + 여유
) -> None:
    """
    실제 사용량 반영 (LLM 응답 완료 후). Redis 장애 시 조용히 무시 (fail-open).
    cost_cents 는 소수점 허용 → 내부에서 반올림해 정수 누적.
    """
    if tokens <= 0 and cost_cents <= 0:
        return
    try:
        r = get_redis()
        pipe = r.pipeline()
        tkey = _tokens_key(tenant_id)
        ckey = _cost_key(tenant_id)
        if tokens > 0:
            pipe.incrby(tkey, tokens)
            pipe.expire(tkey, ttl_seconds)
        if cost_cents > 0:
            pipe.incrby(ckey, round(cost_cents))
            pipe.expire(ckey, ttl_seconds)
        await pipe.execute()
    except RedisError as e:
        logger.warning(
            "quota.commit_failed",
            tenant_id=tenant_id,
            error=str(e),
            tokens=tokens,
            cost_cents=cost_cents,
        )
