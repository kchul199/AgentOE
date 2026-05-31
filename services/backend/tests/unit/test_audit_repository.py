"""Unit tests for Audit Repository.

WORM(Write-Once-Read-Many) 보장 계층 중 "애플리케이션 계층" 을 검증한다.
스토리지 / 권한 계층은 MongoDB 쪽이므로 여기서는 **AuditRepository 가
update/delete/drop 메서드를 노출하지 않음** 을 확인하면 충분.
"""

from unittest.mock import AsyncMock

import pytest

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


# ── WORM 애플리케이션-계층 보장 ────────────────────────────────────────────────


class TestWORMGuarantees:
    """AuditRepository 가 감사 기록을 수정/삭제할 수 있는 API 를 노출하지 않는지."""

    def test_no_update_method(self):
        """repository 에 update 계열 메서드가 없어야 함."""
        repo = AuditRepository()
        forbidden = [
            "update",
            "update_one",
            "update_many",
            "replace",
            "replace_one",
            "find_one_and_update",
            "find_one_and_replace",
        ]
        for name in forbidden:
            assert not hasattr(repo, name), (
                f"AuditRepository 가 '{name}' 메서드를 노출함 — WORM 보장 위반 위험."
            )

    def test_no_delete_method(self):
        """repository 에 delete/drop 계열 메서드가 없어야 함."""
        repo = AuditRepository()
        forbidden = [
            "delete",
            "delete_one",
            "delete_many",
            "remove",
            "find_one_and_delete",
            "drop",
            "drop_collection",
        ]
        for name in forbidden:
            assert not hasattr(repo, name), (
                f"AuditRepository 가 '{name}' 메서드를 노출함 — WORM 보장 위반 위험."
            )

    def test_public_surface_is_log_and_query_only(self):
        """공개 API 는 log (쓰기) 와 query (읽기) 두 개 뿐이어야 한다."""
        repo = AuditRepository()
        # @property 는 메서드가 아니므로 제외한다 (col 이 여기에 해당)
        public = {
            name
            for name in dir(repo)
            if not name.startswith("_")
            and callable(getattr(repo, name))
            and not isinstance(getattr(type(repo), name, None), property)
        }
        # log / query 외에 다른 공개 메서드가 추가되면 이 테스트가 실패해
        # WORM 보장을 재검토하도록 유도.
        assert public == {"log", "query"}, (
            f"AuditRepository 공개 메서드가 예상과 다름: {public}. "
            "새 메서드를 추가하려면 WORM 계층이 유지되는지 재검토 필요."
        )
