"""Unit tests for Kill Switch."""

from unittest.mock import AsyncMock, patch

import pytest

from app.domain.kill_switch import KillSwitchScope, KillSwitchService


@pytest.fixture
def mock_col():
    col = AsyncMock()
    col.find_one = AsyncMock(return_value=None)
    col.insert_one = AsyncMock()
    col.find_one_and_update = AsyncMock(return_value=None)
    return col


@pytest.fixture
def service(mock_col):
    svc = KillSwitchService()
    svc._col = mock_col
    # patch col property
    type(svc).col = property(lambda self: mock_col)
    return svc


@pytest.mark.asyncio
async def test_is_active_returns_false_when_no_switch():
    service = KillSwitchService()
    with (
        patch("app.domain.kill_switch.get_kill_switch_cached", return_value=None),
        patch.object(
            type(service),
            "col",
            new_callable=lambda: property(
                lambda self: AsyncMock(find_one=AsyncMock(return_value=None))
            ),
        ),
    ):
        result = await service.is_active(KillSwitchScope.TENANT, "tenant-x")
        assert result is False


@pytest.mark.asyncio
async def test_is_active_returns_cached_true():
    service = KillSwitchService()
    with patch("app.domain.kill_switch.get_kill_switch_cached", return_value=True):
        result = await service.is_active(KillSwitchScope.TENANT, "tenant-x")
        assert result is True


@pytest.mark.asyncio
async def test_is_active_returns_cached_false():
    service = KillSwitchService()
    with patch("app.domain.kill_switch.get_kill_switch_cached", return_value=False):
        result = await service.is_active(KillSwitchScope.FEATURE, "stt")
        assert result is False
