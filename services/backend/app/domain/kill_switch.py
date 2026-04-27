"""Kill Switch — tenant/feature/scenario level emergency stop."""
from enum import Enum
from datetime import datetime, timezone
from typing import Any

import logging

from app.core.redis_client import cache_kill_switch, get_kill_switch_cached

logger = logging.getLogger(__name__)


class KillSwitchScope(str, Enum):
    TENANT = "tenant"
    FEATURE = "feature"
    SCENARIO = "scenario"


class KillSwitchService:
    """Checks if a Kill Switch is active for a given scope/target."""

    def __init__(self, db=None) -> None:
        self._db = db

    @property
    def col(self):
        from app.core.database import get_database
        db = self._db or get_database()
        return db["kill_switches"]

    async def is_active(self, scope: KillSwitchScope, target_id: str) -> bool:
        """Check if kill switch is active. Checks Redis cache first."""
        # 1. Redis 캐시 확인 (초저지연)
        cached = await get_kill_switch_cached(scope.value, target_id)
        if cached is not None:
            return cached

        # 2. MongoDB 조회 (캐시 미스)
        doc = await self.col.find_one({
            "scope": scope.value,
            "target_id": target_id,
            "active": True,
        })
        active = doc is not None

        # 3. 캐시 갱신
        await cache_kill_switch(scope.value, target_id, active)
        return active

    async def activate(
        self,
        scope: KillSwitchScope,
        target_id: str,
        reason: str,
        activated_by: str,
    ) -> dict[str, Any]:
        import uuid
        switch_id = f"ks_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        doc = {
            "switch_id": switch_id,
            "scope": scope.value,
            "target_id": target_id,
            "reason": reason,
            "activated_by": activated_by,
            "activated_at": datetime.now(timezone.utc),
            "active": True,
        }
        await self.col.insert_one(doc)
        await cache_kill_switch(scope.value, target_id, True)
        logger.warning("Kill switch activated",
                       switch_id=switch_id, scope=scope.value, target_id=target_id)
        return {k: v for k, v in doc.items() if k != "_id"}

    async def deactivate(self, switch_id: str) -> dict[str, Any] | None:
        doc = await self.col.find_one_and_update(
            {"switch_id": switch_id, "active": True},
            {"$set": {"active": False, "deactivated_at": datetime.now(timezone.utc)}},
            return_document=True,
        )
        if doc:
            await cache_kill_switch(doc["scope"], doc["target_id"], False)
            logger.info("Kill switch deactivated")
        return {k: v for k, v in doc.items() if k != "_id"} if doc else None

    async def list_active(self) -> list[dict]:
        cursor = self.col.find({"active": True}, {"_id": 0})
        return await cursor.to_list(length=100)
