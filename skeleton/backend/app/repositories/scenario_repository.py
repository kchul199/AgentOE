"""MongoDB repository for `scenarios` collection.

멀티테넌시:
    - 모든 쿼리에 `tenant_id` 필수. 다른 테넌트의 시나리오에 접근할 수 없다.
    - (tenant_id, scenario_id, version) 조합이 유일 키.

버전 관리:
    - save() 는 동일 scenario_id 의 최신 버전을 조회해 +1 한 새 문서를 생성한다.
    - `published=True` 인 버전은 한 scenario_id 당 0 또는 1개 — publish() 가 기존
      published=True 문서를 모두 False 로 내리고 지정 버전만 True 로 올린다.
    - get_latest() / get_published() / get_version() 3종 조회 모두 제공.

Index 설계 (mongo/init-tenants.js 에서 생성):
    { tenant_id: 1, scenario_id: 1, version: -1 }  — 내림차순으로 latest 고속 조회
    { tenant_id: 1, scenario_id: 1, published: 1 } — published=True 단일 행 조회
    { tenant_id: 1, updated_at: -1 }               — 테넌트별 최근 목록
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import get_database
from app.core.exceptions import AgentOEBaseError


class ScenarioNotFoundError(AgentOEBaseError):
    http_status = 404
    code = "SCENARIO_NOT_FOUND"

    def __init__(self, tenant_id: str, scenario_id: str, version: int | str):
        super().__init__(
            f"Scenario '{scenario_id}' v{version} not found for tenant '{tenant_id}'",
        )


class ScenarioConflictError(AgentOEBaseError):
    http_status = 409
    code = "SCENARIO_CONFLICT"


class ScenarioRepository:
    """비동기 MongoDB repository (motor)."""

    def __init__(self, db=None) -> None:
        self._db = db

    @property
    def col(self):
        return (self._db or get_database())["scenarios"]

    # ── 조회 ─────────────────────────────────────────────────────────

    async def list_by_tenant(
        self, tenant_id: str, *, include_drafts: bool = True, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """테넌트의 시나리오 최신 버전만 요약 목록으로 반환."""
        pipeline: list[dict[str, Any]] = [
            {"$match": {"tenant_id": tenant_id}},
            {"$sort": {"scenario_id": 1, "version": -1}},
            {
                "$group": {
                    "_id": "$scenario_id",
                    "doc": {"$first": "$$ROOT"},
                },
            },
            {"$replaceRoot": {"newRoot": "$doc"}},
            {"$sort": {"updated_at": -1}},
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "scenario_id": 1,
                    "name": 1,
                    "version": 1,
                    "published": 1,
                    "updated_at": 1,
                    "tags": 1,
                },
            },
        ]
        if not include_drafts:
            pipeline.insert(1, {"$match": {"published": True}})
        cursor = self.col.aggregate(pipeline)
        return await cursor.to_list(length=limit)

    async def get_version(
        self, tenant_id: str, scenario_id: str, version: int,
    ) -> dict[str, Any]:
        doc = await self.col.find_one(
            {"tenant_id": tenant_id, "scenario_id": scenario_id, "version": version},
            {"_id": 0},
        )
        if not doc:
            raise ScenarioNotFoundError(tenant_id, scenario_id, version)
        return doc

    async def get_latest(
        self, tenant_id: str, scenario_id: str,
    ) -> dict[str, Any]:
        doc = await self.col.find_one(
            {"tenant_id": tenant_id, "scenario_id": scenario_id},
            {"_id": 0},
            sort=[("version", -1)],
        )
        if not doc:
            raise ScenarioNotFoundError(tenant_id, scenario_id, "latest")
        return doc

    async def get_published(
        self, tenant_id: str, scenario_id: str,
    ) -> dict[str, Any]:
        """스냅샷 엔진이 로드하는 유일한 경로. published=True 가 없으면 404."""
        doc = await self.col.find_one(
            {
                "tenant_id": tenant_id,
                "scenario_id": scenario_id,
                "published": True,
            },
            {"_id": 0},
        )
        if not doc:
            raise ScenarioNotFoundError(tenant_id, scenario_id, "published")
        return doc

    # ── 저장 ─────────────────────────────────────────────────────────

    async def save(self, scenario: dict[str, Any]) -> dict[str, Any]:
        """
        scenario dict (Scenario DSL + tenant_id) 를 새 버전으로 insert.

        호출 규칙:
          - scenario["tenant_id"], ["scenario_id"] 필수
          - scenario["version"] 은 무시되고 서버측에서 `latest.version + 1` 로 결정
          - scenario["published"] 는 저장 시 항상 False (명시적 publish() 필요)
        """
        tenant_id = scenario["tenant_id"]
        scenario_id = scenario["scenario_id"]

        latest = await self.col.find_one(
            {"tenant_id": tenant_id, "scenario_id": scenario_id},
            {"version": 1},
            sort=[("version", -1)],
        )
        next_version = (latest["version"] + 1) if latest else 1

        now = datetime.now(timezone.utc)
        doc = dict(scenario)
        doc["version"] = next_version
        doc["published"] = False
        doc["created_at"] = doc.get("created_at") or now.isoformat()
        doc["updated_at"] = now.isoformat()

        await self.col.insert_one(doc)
        return {k: v for k, v in doc.items() if k != "_id"}

    async def publish(
        self, tenant_id: str, scenario_id: str, version: int,
    ) -> dict[str, Any]:
        """
        지정 버전만 published=True 로 올리고 같은 scenario_id 의 다른 버전은 False 로.

        두 개의 write 를 하지만 MongoDB 는 단일 컬렉션 연속 업데이트로 충분.
        (완전 원자성 필요 시 transactions + replica set 로 업그레이드)
        """
        # 1. 대상 버전 존재 확인
        target = await self.col.find_one(
            {"tenant_id": tenant_id, "scenario_id": scenario_id, "version": version},
            {"_id": 0, "scenario_id": 1, "version": 1},
        )
        if not target:
            raise ScenarioNotFoundError(tenant_id, scenario_id, version)

        # 2. 기존 published 버전들 내리기
        await self.col.update_many(
            {
                "tenant_id": tenant_id,
                "scenario_id": scenario_id,
                "published": True,
            },
            {"$set": {"published": False}},
        )
        # 3. 대상 버전 올리기
        now = datetime.now(timezone.utc).isoformat()
        result = await self.col.find_one_and_update(
            {
                "tenant_id": tenant_id,
                "scenario_id": scenario_id,
                "version": version,
            },
            {"$set": {"published": True, "updated_at": now}},
            return_document=True,
            projection={"_id": 0},
        )
        if not result:
            # 사실상 도달 불가 (1 단계에서 확인했음) — 경합 안전망
            raise ScenarioConflictError(
                f"publish failed: {scenario_id} v{version} disappeared during publish",
            )
        return result

    async def delete_version(
        self, tenant_id: str, scenario_id: str, version: int,
    ) -> None:
        """
        단일 버전 삭제. published=True 버전은 삭제 거부 (먼저 다른 버전을 publish).
        """
        target = await self.col.find_one(
            {"tenant_id": tenant_id, "scenario_id": scenario_id, "version": version},
            {"_id": 0, "published": 1},
        )
        if not target:
            raise ScenarioNotFoundError(tenant_id, scenario_id, version)
        if target.get("published"):
            raise ScenarioConflictError(
                "published version cannot be deleted — publish another version first",
            )
        await self.col.delete_one(
            {"tenant_id": tenant_id, "scenario_id": scenario_id, "version": version},
        )
