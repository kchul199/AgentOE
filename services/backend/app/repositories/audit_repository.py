"""Audit log repository for MongoDB Time Series collection.

WORM 보장 (Write-Once-Read-Many):
  - 스토리지 계층: `mongo/init_schema.js` 가 audit_events 를 Time Series 콜렉션으로 생성.
    Time Series 는 내부적으로 system.buckets.* 에 저장되며 개별 문서 update/delete 가
    제한적이고 비효율적 — 구조적으로 append-only 에 가까움.
  - 권한 계층: `mongo/create_audit_role.js` 가 auditWriter role 을 생성.
    이 role 은 insert/find 만 허용 — update/remove/drop action 이 **빠져있음**.
  - 애플리케이션 계층: 이 클래스는 `insert_one` 과 `aggregate` 만 호출.
    update/delete/drop 메서드를 **의도적으로 제공하지 않음** — 타 개발자가
    실수로 감사 기록을 수정하지 못하도록.

이 3계층 중 하나가 무너져도 다른 둘이 남아있도록 defense-in-depth.
"""
from datetime import datetime, timezone
from typing import Any

import logging

from app.core.database import get_database

logger = logging.getLogger(__name__)


class AuditRepository:
    """Write-only repository for audit_events Time Series collection.

    Do NOT add update/delete/drop methods to this class.
    모든 수정은 새 `log()` 호출로 append 해야 합니다.
    """

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
        """Append an audit event (fire-and-forget pattern).

        이 메서드의 예외는 **삼켜짐** — 감사 로그 쓰기 실패가 메인 통화 흐름을
        방해해서는 안 되기 때문. 그러나 로그는 반드시 ERROR 레벨로 남겨
        DLQ / Prometheus 로 관찰 가능하게 할 것.
        """
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
