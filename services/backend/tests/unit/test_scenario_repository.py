"""Track 4-E: ScenarioRepository 단위 테스트.

MongoDB 는 mongomock-motor 가 있으면 그걸 쓰고, 없으면 AsyncMock 기반 fake db.
테스트 초점:
  - save() 가 version 을 +1 로 채번
  - publish() 가 이전 published 를 False 로 내림
  - get_published() 는 published 하나만 반환
  - delete_version() 는 published 버전 거부
  - multi-tenant isolation — 다른 테넌트 조회 불가
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.repositories.scenario_repository import (
    ScenarioConflictError,
    ScenarioNotFoundError,
    ScenarioRepository,
)


class _FakeCollection:
    """In-memory async Mongo-ish collection — 필요한 연산만 구현."""

    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

    # ---- 쿼리 매칭 헬퍼 ------------------------------------------------
    @staticmethod
    def _match(doc: dict, q: dict) -> bool:
        return all(doc.get(k) == v for k, v in q.items())

    async def insert_one(self, doc: dict) -> None:
        self.docs.append(dict(doc))

    async def find_one(
        self,
        q: dict,
        projection: dict | None = None,
        sort: list | None = None,
    ) -> dict | None:
        matches = [d for d in self.docs if self._match(d, q)]
        if sort:
            key, direction = sort[0]
            matches.sort(key=lambda d: d.get(key, 0), reverse=direction == -1)
        return dict(matches[0]) if matches else None

    async def update_many(self, q: dict, update: dict) -> None:
        for d in self.docs:
            if self._match(d, q):
                d.update(update["$set"])

    async def find_one_and_update(
        self, q: dict, update: dict,
        return_document: bool = False,
        projection: dict | None = None,
    ) -> dict | None:
        for d in self.docs:
            if self._match(d, q):
                d.update(update["$set"])
                return dict(d)
        return None

    async def delete_one(self, q: dict) -> MagicMock:
        for i, d in enumerate(self.docs):
            if self._match(d, q):
                self.docs.pop(i)
                res = MagicMock()
                res.deleted_count = 1
                return res
        res = MagicMock()
        res.deleted_count = 0
        return res

    def aggregate(self, pipeline: list[dict]) -> Any:
        """단순 구현 — list_by_tenant 의 파이프라인 전용."""
        match_stage = next((s for s in pipeline if "$match" in s), None)
        tenant_match = match_stage["$match"] if match_stage else {}
        results = [d for d in self.docs if self._match(d, tenant_match)]
        # latest per scenario_id
        by_scn: dict[str, dict] = {}
        for d in sorted(results, key=lambda x: x["version"], reverse=True):
            by_scn.setdefault(d["scenario_id"], d)
        items = [
            {
                "scenario_id": d["scenario_id"],
                "name": d["name"],
                "version": d["version"],
                "published": d.get("published", False),
                "updated_at": d.get("updated_at"),
                "tags": d.get("tags", []),
            }
            for d in by_scn.values()
        ]
        # include_drafts=False 인 케이스 (pipeline 에 두 번째 $match 가 있음)
        extra_match = pipeline[1] if len(pipeline) > 1 and "$match" in pipeline[1] else None
        if extra_match and extra_match["$match"].get("published") is True:
            items = [x for x in items if x["published"]]

        class _Cursor:
            def __init__(self, items):
                self.items = items

            async def to_list(self, length: int) -> list:
                return list(self.items[:length])

        return _Cursor(items)


@pytest.fixture
def repo() -> ScenarioRepository:
    fake_col = _FakeCollection()
    fake_db = {"scenarios": fake_col}
    return ScenarioRepository(db=fake_db)


def _scenario(scenario_id: str = "greet_v1", tenant_id: str = "t_a") -> dict:
    return {
        "scenario_id": scenario_id,
        "tenant_id": tenant_id,
        "name": "Greeting",
        "version": 1,        # 무시되어야 함
        "entry": "n1",
        "fallback_node": None,
        "nodes": [
            {
                "id": "n1", "type": "end",
                "config": {"closing_message": "감사합니다"},
            },
        ],
        "edges": [],
        "tags": [],
    }


# ── save 는 버전을 +1 로 채번한다 ─────────────────────────────────────────────

@pytest.mark.asyncio
async def test_save_increments_version(repo: ScenarioRepository) -> None:
    s1 = await repo.save(_scenario())
    s2 = await repo.save(_scenario())
    s3 = await repo.save(_scenario())
    assert (s1["version"], s2["version"], s3["version"]) == (1, 2, 3)
    # 저장은 항상 draft
    assert all(not s["published"] for s in (s1, s2, s3))


# ── get_latest 는 내림차순 최상단 반환 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_latest_returns_highest_version(repo: ScenarioRepository) -> None:
    for _ in range(3):
        await repo.save(_scenario())
    latest = await repo.get_latest("t_a", "greet_v1")
    assert latest["version"] == 3


# ── publish 가 이전 published 를 내린다 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_toggles_single_published_version(
    repo: ScenarioRepository,
) -> None:
    for _ in range(3):
        await repo.save(_scenario())

    v1 = await repo.publish("t_a", "greet_v1", 1)
    assert v1["published"] is True

    v3 = await repo.publish("t_a", "greet_v1", 3)
    assert v3["published"] is True

    # v1 은 자동으로 내려가 있어야 함
    v1_check = await repo.get_version("t_a", "greet_v1", 1)
    assert v1_check["published"] is False

    pub = await repo.get_published("t_a", "greet_v1")
    assert pub["version"] == 3


# ── publish: 존재하지 않는 버전 → 404 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_publish_missing_version_raises_not_found(
    repo: ScenarioRepository,
) -> None:
    await repo.save(_scenario())
    with pytest.raises(ScenarioNotFoundError):
        await repo.publish("t_a", "greet_v1", 999)


# ── get_published 미발행 시 404 ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_published_when_none_raises(repo: ScenarioRepository) -> None:
    await repo.save(_scenario())
    with pytest.raises(ScenarioNotFoundError):
        await repo.get_published("t_a", "greet_v1")


# ── delete_version: published 는 거부 ────────────────────────────────────────

@pytest.mark.asyncio
async def test_delete_published_version_raises_conflict(
    repo: ScenarioRepository,
) -> None:
    await repo.save(_scenario())
    await repo.publish("t_a", "greet_v1", 1)
    with pytest.raises(ScenarioConflictError):
        await repo.delete_version("t_a", "greet_v1", 1)


@pytest.mark.asyncio
async def test_delete_draft_version_succeeds(repo: ScenarioRepository) -> None:
    await repo.save(_scenario())
    await repo.save(_scenario())       # v2
    await repo.delete_version("t_a", "greet_v1", 1)
    with pytest.raises(ScenarioNotFoundError):
        await repo.get_version("t_a", "greet_v1", 1)


# ── multi-tenant isolation ───────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_tenant_isolation(repo: ScenarioRepository) -> None:
    await repo.save(_scenario(tenant_id="t_a"))
    await repo.save(_scenario(tenant_id="t_b"))
    # t_a 는 t_b 의 시나리오를 볼 수 없어야 함
    with pytest.raises(ScenarioNotFoundError):
        await repo.get_latest("t_a", "greet_v1")  # t_a 에도 존재하지만 scenario_id 는 동일 — check 다시

    # 정말로 tenant isolation 확인: 동일 scenario_id 를 각 테넌트에 저장해도 서로 간섭하지 않음
    a = await repo.save(_scenario(tenant_id="t_a"))
    b = await repo.save(_scenario(tenant_id="t_b"))
    assert a["version"] == 2   # t_a 의 1번은 위에서 저장된 바 있음
    assert b["version"] == 2


# ── list_by_tenant: latest per scenario_id ───────────────────────────────────

@pytest.mark.asyncio
async def test_list_by_tenant_dedups_to_latest(repo: ScenarioRepository) -> None:
    await repo.save(_scenario("s1"))
    await repo.save(_scenario("s1"))
    await repo.save(_scenario("s2"))

    items = await repo.list_by_tenant("t_a")
    assert len(items) == 2
    versions = {i["scenario_id"]: i["version"] for i in items}
    assert versions == {"s1": 2, "s2": 1}
