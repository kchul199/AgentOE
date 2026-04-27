"""MongoDB repository for tenants collection."""
from datetime import datetime, timezone
from typing import Any

from app.core.database import get_database
from app.core.exceptions import TenantNotFoundError


class TenantRepository:
    def __init__(self, db=None):
        self._db = db

    @property
    def col(self):
        return (self._db or get_database())["tenants"]

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        data["created_at"] = datetime.now(timezone.utc)
        data.setdefault("enabled", True)
        data.setdefault("plan", "standard")
        data.setdefault("max_sessions", 100)
        data.setdefault("features", ["stt", "llm", "tts"])
        await self.col.insert_one(data)
        return {k: v for k, v in data.items() if k != "_id"}

    async def get_by_id(self, tenant_id: str) -> dict[str, Any]:
        doc = await self.col.find_one({"tenant_id": tenant_id}, {"_id": 0})
        if not doc:
            raise TenantNotFoundError(tenant_id)
        return doc

    async def list_all(self, enabled_only: bool = False) -> list[dict]:
        query = {"enabled": True} if enabled_only else {}
        cursor = self.col.find(query, {"_id": 0}).sort("created_at", -1)
        return await cursor.to_list(length=500)

    async def update(self, tenant_id: str, update: dict[str, Any]) -> dict[str, Any]:
        update["updated_at"] = datetime.now(timezone.utc)
        doc = await self.col.find_one_and_update(
            {"tenant_id": tenant_id},
            {"$set": update},
            return_document=True,
            projection={"_id": 0},
        )
        if not doc:
            raise TenantNotFoundError(tenant_id)
        return doc

    async def delete(self, tenant_id: str) -> None:
        result = await self.col.delete_one({"tenant_id": tenant_id})
        if result.deleted_count == 0:
            raise TenantNotFoundError(tenant_id)
