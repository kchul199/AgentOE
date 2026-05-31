"""Unit tests for Circuit Breaker state machine."""

import asyncio
import contextlib
from unittest.mock import AsyncMock

import pytest

from app.domain.circuit_breaker import (
    CircuitBreaker,
    CircuitBreakerConfig,
    CircuitBreakerOpenError,
    CircuitState,
)


@pytest.fixture
def cb():
    config = CircuitBreakerConfig(
        failure_threshold=3,
        recovery_timeout=0.1,  # 테스트용 짧은 타임아웃
        half_open_max_calls=2,
        success_threshold=2,
    )
    return CircuitBreaker("test-service", config)


@pytest.mark.asyncio
async def test_initial_state_closed(cb):
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_stays_closed_on_success(cb):
    mock_fn = AsyncMock(return_value="ok")
    result = await cb.call(mock_fn)
    assert result == "ok"
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_opens_after_threshold(cb):
    mock_fn = AsyncMock(side_effect=RuntimeError("fail"))
    for _ in range(3):
        with contextlib.suppress(RuntimeError):
            await cb.call(mock_fn)
    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_open_rejects_immediately(cb):
    mock_fn = AsyncMock(side_effect=RuntimeError("fail"))
    for _ in range(3):
        with contextlib.suppress(RuntimeError):
            await cb.call(mock_fn)

    with pytest.raises(CircuitBreakerOpenError):
        await cb.call(AsyncMock(return_value="ok"))


@pytest.mark.asyncio
async def test_transitions_to_half_open(cb):
    mock_fn = AsyncMock(side_effect=RuntimeError("fail"))
    for _ in range(3):
        with contextlib.suppress(RuntimeError):
            await cb.call(mock_fn)
    assert cb.state == CircuitState.OPEN

    import asyncio

    await asyncio.sleep(0.15)  # recovery_timeout 대기

    success_fn = AsyncMock(return_value="ok")
    result = await cb.call(success_fn)
    assert result == "ok"


@pytest.mark.asyncio
async def test_closed_after_recovery(cb):
    fail_fn = AsyncMock(side_effect=RuntimeError("fail"))
    for _ in range(3):
        with contextlib.suppress(RuntimeError):
            await cb.call(fail_fn)

    await asyncio.sleep(0.15)

    success_fn = AsyncMock(return_value="recovered")
    for _ in range(2):  # success_threshold=2
        await cb.call(success_fn)
    assert cb.state == CircuitState.CLOSED


@pytest.mark.asyncio
async def test_stats_tracking(cb):
    fail_fn = AsyncMock(side_effect=ValueError("err"))
    for _ in range(2):
        with contextlib.suppress(ValueError):
            await cb.call(fail_fn)
    assert cb.stats.total_calls == 2
    assert cb.stats.total_failures == 2
    assert cb.stats.failure_count == 2


@pytest.mark.asyncio
async def test_manual_reset(cb):
    fail_fn = AsyncMock(side_effect=RuntimeError("fail"))
    for _ in range(3):
        with contextlib.suppress(RuntimeError):
            await cb.call(fail_fn)
    assert cb.state == CircuitState.OPEN
    cb.reset()
    assert cb.state == CircuitState.CLOSED
