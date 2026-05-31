"""MongoDB repository for connectors collection."""

import uuid
from datetime import UTC, datetime
from typing import Any

from app.core.database import get_database
from app.core.exceptions import AgentOEBaseError


class ConnectorNotFoundError(AgentOEBaseError):
    http_status = 404
    code = "CONNECTOR_NOT_FOUND"

    def __init__(self, connector_id: str):
        super().__init__(f"Connector '{connector_id}' not found")


class ConnectorRepository:
    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def col(self) -> Any:
        return (self._db or get_database())["connectors"]

    async def create(self, data: dict[str, Any]) -> dict[str, Any]:
        data.setdefault("connector_id", f"conn_{uuid.uuid4().hex[:12]}")
        data["created_at"] = datetime.now(UTC)
        data.setdefault("enabled", True)
        data.setdefault("whitelist", [])
        await self.col.insert_one(data)
        return {k: v for k, v in data.items() if k != "_id"}

    async def get_by_id(self, connector_id: str) -> dict[str, Any]:
        doc = await self.col.find_one({"connector_id": connector_id}, {"_id": 0})
        if not doc:
            raise ConnectorNotFoundError(connector_id)
        return doc

    async def list_by_tenant(self, tenant_id: str) -> list[dict]:
        cursor = self.col.find({"tenant_id": tenant_id}, {"_id": 0}).sort("created_at", -1)
        return await cursor.to_list(length=200)

    async def update(self, connector_id: str, update: dict[str, Any]) -> dict[str, Any]:
        update["updated_at"] = datetime.now(UTC)
        doc = await self.col.find_one_and_update(
            {"connector_id": connector_id},
            {"$set": update},
            return_document=True,
            projection={"_id": 0},
        )
        if not doc:
            raise ConnectorNotFoundError(connector_id)
        return doc

    async def delete(self, connector_id: str) -> None:
        result = await self.col.delete_one({"connector_id": connector_id})
        if result.deleted_count == 0:
            raise ConnectorNotFoundError(connector_id)
