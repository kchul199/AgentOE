"""Audit log repository for MongoDB Time Series collection."""
from datetime import datetime, timezone
from typing import Any

import logging

from app.core.database import get_database

logger = logging.getLogger(__name__)


class AuditRepository:
    """Write-only repository for audit_events Time Series collection."""

    def __init__(self, db=None) -> None:
        self._db = db

    @property
    def col(self):
        db = self._db or get_database()
        return db["audit_events"]

    async def log(
        self,
        event_type: str,
        tenant_id: str,
        session_id: str | None = None,
        actor: str = "system",
        details: dict[str, Any] | None = None,
    ) -> None:
        """Append an audit event (fire-and-forget pattern)."""
        doc = {
            "timestamp": datetime.now(timezone.utc),  # timeField
            "metadata": {                              # metaField
                "tenant_id": tenant_id,
                "session_id": session_id,
                "event_type": event_type,
            },
            "actor": actor,
            "details": details or {},
        }
        try:
            await self.col.insert_one(doc)
        except Exception as e:
            # 감사 로그 실패가 메인 흐름을 막지 않도록
            logger.error("Audit log write failed: %s (event_type=%s)", str(e), event_type)

    async def query(
        self,
        tenant_id: str,
        session_id: str | None = None,
        event_type: str | None = None,
        from_dt: datetime | None = None,
        to_dt: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        match: dict[str, Any] = {"metadata.tenant_id": tenant_id}
        if session_id:
            match["metadata.session_id"] = session_id
        if event_type:
            match["metadata.event_type"] = event_type
        if from_dt or to_dt:
            ts_filter: dict = {}
            if from_dt:
                ts_filter["$gte"] = from_dt
            if to_dt:
                ts_filter["$lte"] = to_dt
            match["timestamp"] = ts_filter

        pipeline = [
            {"$match": match},
            {"$sort": {"timestamp": -1}},
            {"$facet": {
                "items": [{"$skip": offset}, {"$limit": limit},
                          {"$project": {"_id": 0}}],
                "total": [{"$count": "count"}],
            }},
        ]
        result = await self.col.aggregate(pipeline).to_list(1)
        if not result:
            return [], 0
        data = result[0]
        total = data["total"][0]["count"] if data["total"] else 0
        return data["items"], total
