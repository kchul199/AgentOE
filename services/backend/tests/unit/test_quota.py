"""Track 2-d: LLM 토큰/비용 일일 쿼터 단위 테스트.

Redis 는 fakeredis 가 있으면 그걸 쓰고, 없으면 async Mock 으로 대체.
kinetic path:
  check_quota 는 단순 조회, enforce_quota 는 policy 분기,
  commit_usage 는 pipeline INCRBY+EXPIRE.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import quota as quota_mod


@pytest.fixture
def fake_redis(monkeypatch):
    """quota.py 의 get_redis() 를 가로채 AsyncMock 반환."""
    r = MagicMock()
    r.mget = AsyncMock(return_value=[None, None])
    pipe = MagicMock()
    pipe.incrby = MagicMock()
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[])
    r.pipeline = MagicMock(return_value=pipe)
    monkeypatch.setattr(quota_mod, "get_redis", lambda: r)
    # 기본 설정: 쿼터 활성, 5M tokens / 1000 cents
    fake_settings = SimpleNamespace(
        LLM_QUOTA_ENABLED=True,
        LLM_DAILY_TOKEN_QUOTA_DEFAULT=5_000_000,
        LLM_DAILY_COST_QUOTA_CENTS_DEFAULT=1000,
        LLM_QUOTA_EXCEEDED_BEHAVIOR="fallback",
    )
    monkeypatch.setattr(quota_mod, "settings", fake_settings)
    return r


@pytest.mark.asyncio
async def test_check_quota_empty_returns_zeros(fake_redis) -> None:
    status = await quota_mod.check_quota("tenant-A")
    assert status.tokens_used_today == 0
    assert status.cost_cents_used_today == 0
    assert not status.over


@pytest.mark.asyncio
async def test_check_quota_over_tokens(fake_redis) -> None:
    fake_redis.mget = AsyncMock(return_value=[b"5000001", b"0"])
    status = await quota_mod.check_quota("tenant-A")
    assert status.over_tokens is True
    assert status.over is True


@pytest.mark.asyncio
async def test_enforce_quota_fallback_raises_graceful(
    fake_redis,
    monkeypatch,
) -> None:
    fake_redis.mget = AsyncMock(return_value=[b"5000001", b"0"])
    with pytest.raises(quota_mod.QuotaExceededError) as exc_info:
        await quota_mod.enforce_quota("tenant-A")
    assert exc_info.value.graceful is True
    assert exc_info.value.scope == "tokens"


@pytest.mark.asyncio
async def test_enforce_quota_reject_raises_non_graceful(
    fake_redis,
    monkeypatch,
) -> None:
    fake_redis.mget = AsyncMock(return_value=[b"0", b"1001"])  # cost 초과
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_QUOTA_EXCEEDED_BEHAVIOR",
        "reject",
    )
    with pytest.raises(quota_mod.QuotaExceededError) as exc_info:
        await quota_mod.enforce_quota("tenant-A")
    assert exc_info.value.graceful is False
    assert exc_info.value.scope == "cost"


@pytest.mark.asyncio
async def test_enforce_quota_warn_only_passes(fake_redis, monkeypatch) -> None:
    fake_redis.mget = AsyncMock(return_value=[b"5000001", b"0"])
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_QUOTA_EXCEEDED_BEHAVIOR",
        "warn",
    )
    # warn 정책은 예외 없이 status 반환
    status = await quota_mod.enforce_quota("tenant-A")
    assert status.over_tokens is True


@pytest.mark.asyncio
async def test_enforce_quota_disabled_short_circuits(
    fake_redis,
    monkeypatch,
) -> None:
    monkeypatch.setattr(quota_mod.settings, "LLM_QUOTA_ENABLED", False)
    status = await quota_mod.enforce_quota("tenant-A")
    # 비활성 시 Redis 조회도 하지 않음 (0/0 상태)
    assert status.tokens_used_today == 0
    fake_redis.mget.assert_not_called()


@pytest.mark.asyncio
async def test_commit_usage_pipelines_incr_and_expire(fake_redis) -> None:
    await quota_mod.commit_usage("tenant-A", tokens=100, cost_cents=0.3)
    pipe = fake_redis.pipeline.return_value
    # 토큰/비용 모두 incrby 호출
    incr_calls = [c.args for c in pipe.incrby.call_args_list]
    assert any(args[1] == 100 for args in incr_calls)
    # cost_cents=0.3 → round(0.3) = 0 은 증가 생략되지만 0.3 은 > 0 이므로 반영
    # (정수 올림/반올림 정책은 int(round(x)))
    pipe.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_commit_usage_zero_noop(fake_redis) -> None:
    await quota_mod.commit_usage("tenant-A", tokens=0, cost_cents=0)
    # 둘 다 0 이면 Redis 접근 자체를 생략
    fake_redis.pipeline.assert_not_called()


@pytest.mark.asyncio
async def test_check_quota_fail_open_on_redis_error(
    fake_redis,
    monkeypatch,
) -> None:
    fake_redis.mget = AsyncMock(side_effect=quota_mod.RedisError("boom"))
    status = await quota_mod.check_quota("tenant-A")
    # fail-open: 에러가 있어도 0/0 반환, over=False
    assert status.tokens_used_today == 0
    assert status.over is False
