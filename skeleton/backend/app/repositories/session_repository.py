"""MongoDB Motor repository for sessions collection."""
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import ASCENDING, DESCENDING

from app.core.database import get_database
from app.core.exceptions import SessionNotFoundError


class SessionRepository:
    """CRUD operations for the sessions MongoDB collection."""

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    @property
    def col(self):
        db = self._db or get_database()
        return db["sessions"]

    async def create(self, session_data: dict[str, Any]) -> dict[str, Any]:
        session_data["created_at"] = datetime.now(timezone.utc)
        session_data["updated_at"] = session_data["created_at"]
        await self.col.insert_one(session_data)
        return session_data

    async def get_by_id(self, session_id: str) -> dict[str, Any]:
        doc = await self.col.find_one({"session_id": session_id}, {"_id": 0})
        if not doc:
            raise SessionNotFoundError(session_id)
        return doc

    async def update_state(self, session_id: str, state: str, extra: dict | None = None) -> None:
        update = {"status": state, "updated_at": datetime.now(timezone.utc)}
        if extra:
            update.update(extra)
        result = await self.col.update_one({"session_id": session_id}, {"$set": update})
        if result.matched_count == 0:
            raise SessionNotFoundError(session_id)

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        query: dict[str, Any] = {"tenant_id": tenant_id}
        if status:
            query["status"] = status
        total = await self.col.count_documents(query)
        cursor = (
            self.col.find(query, {"_id": 0})
            .sort("created_at", DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        items = await cursor.to_list(length=limit)
        return items, total

    async def end_session(self, session_id: str) -> None:
        await self.update_state(session_id, "ENDED", {"ended_at": datetime.now(timezone.utc)})
