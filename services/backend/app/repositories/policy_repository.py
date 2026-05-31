"""MongoDB repository for policies collection."""

from datetime import UTC, datetime
from typing import Any

from app.core.database import get_database
from app.core.exceptions import AgentOEBaseError


class PolicyNotFoundError(AgentOEBaseError):
    http_status = 404
    code = "POLICY_NOT_FOUND"

    def __init__(self, policy_id: str):
        super().__init__(f"Policy '{policy_id}' not found")


class PolicyRepository:
    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def col(self) -> Any:
        return (self._db or get_database())["policies"]

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        data["created_at"] = datetime.now(UTC)
        data["updated_at"] = data["created_at"]
        data.setdefault("enabled", True)
        await self.col.insert_one(data)
        return {k: v for k, v in data.items() if k != "_id"}

    async def get_by_id(self, policy_id: str) -> dict[str, Any]:
        doc = await self.col.find_one({"policy_id": policy_id}, {"_id": 0})
        if not doc:
            raise PolicyNotFoundError(policy_id)
        return doc

    async def list_by_tenant(self, tenant_id: str, enabled_only: bool = True) -> list[dict]:
        query: dict[str, Any] = {"tenant_id": tenant_id}
        if enabled_only:
            query["enabled"] = True
        cursor = self.col.find(query, {"_id": 0}).sort("level", 1)
        return await cursor.to_list(length=200)

    async def update(self, policy_id: str, update: dict[str, Any]) -> dict[str, Any]:
        update["updated_at"] = datetime.now(UTC)
        doc = await self.col.find_one_and_update(
            {"policy_id": policy_id},
            {"$set": update},
            return_document=True,
            projection={"_id": 0},
        )
        if not doc:
            raise PolicyNotFoundError(policy_id)
        return doc

    async def delete(self, policy_id: str) -> None:
        result = await self.col.delete_one({"policy_id": policy_id})
        if result.deleted_count == 0:
            raise PolicyNotFoundError(policy_id)
