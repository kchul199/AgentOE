"""Unit tests for Audit Repository."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime, timezone

from app.repositories.audit_repository import AuditRepository


@pytest.mark.asyncio
async def test_log_success():
    repo = AuditRepository()
    mock_col = AsyncMock()
    mock_col.insert_one = AsyncMock()
    type(repo).col = property(lambda self: mock_col)

    await repo.log(
        event_type="SESSION_CREATED",
        tenant_id="tenant-test",
        session_id="sess_001",
        actor="api",
        details={"scenario": "inbound"},
    )
    mock_col.insert_one.assert_called_once()
    call_args = mock_col.insert_one.call_args[0][0]
    assert call_args["metadata"]["event_type"] == "SESSION_CREATED"
    assert call_args["metadata"]["tenant_id"] == "tenant-test"


@pytest.mark.asyncio
async def test_log_does_not_raise_on_db_error():
    """감사 로그 실패가 예외를 발생시키지 않아야 한다."""
    repo = AuditRepository()
    mock_col = AsyncMock()
    mock_col.insert_one = AsyncMock(side_effect=Exception("DB error"))
    type(repo).col = property(lambda self: mock_col)

    # Should not raise
    await repo.log("TEST_EVENT", "tenant-x")
