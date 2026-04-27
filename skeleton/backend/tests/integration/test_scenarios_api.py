"""Track 4-F: Scenarios REST API 통합 테스트.

FastAPI TestClient 기반. `ScenarioRepository` 는 `_FakeCollection` 을 붙여
메모리 몽고로 대체한다. `get_current_tenant` / `require_roles` 는
dependency_overrides 로 우회해 역할별 동작을 검증한다.

커버:
    POST   /api/v1/scenarios/                        — 201 + 서버 채번 version
    POST   /api/v1/scenarios/                        — 422 DSL 오류
    GET    /api/v1/scenarios/{id}?version=latest     — 200
    GET    /api/v1/scenarios/{id}?version=published  — 404 (published 없음)
    GET    /api/v1/scenarios/{id}?version=2          — 200
    GET    /api/v1/scenarios/{id}?version=foo        — 400 invalid
    GET    /api/v1/scenarios/{id}?version=999        — 404 missing
    POST   /api/v1/scenarios/{id}/publish            — 200 admin
    POST   /api/v1/scenarios/{id}/publish            — 403 non-admin
    POST   /api/v1/scenarios/{id}/publish            — 404 missing version
    POST   /api/v1/scenarios/validate                — ok / 이슈 리턴
    DELETE /api/v1/scenarios/{id}/versions/{v}       — 204 draft
    DELETE /api/v1/scenarios/{id}/versions/{v}       — 409 published
    GET    /api/v1/scenarios/                        — 목록
    Tenant spoof (payload.tenant_id 덮어쓰기) 차단   — 저장된 문서 tenant_id 확인
"""
from __future__ import annotations

import sys
import unittest.mock as mock
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# 외부 의존성 mock (DB/Redis 없이 실행)
for _mod in [
    "motor", "motor.motor_asyncio",
    "pymongo", "pymongo.errors",
    "redis", "redis.asyncio",
    "groq", "google.cloud", "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1",
    "grpc",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = mock.MagicMock()


# ── 재사용: 테스트-전용 in-memory Mongo ────────────────────────────────────────
#
# tests/unit/test_scenario_repository.py 의 _FakeCollection 과 동일한 로직을
# 라우터 통합 테스트에서도 사용한다 (동일 구현이 두 레벨에서 검증됨).

class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict[str, Any]] = []

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
                r = MagicMock()
                r.deleted_count = 1
                return r
        r = MagicMock()
        r.deleted_count = 0
        return r

    def aggregate(self, pipeline: list[dict]) -> Any:
        match_stage = next((s for s in pipeline if "$match" in s), None)
        tenant_match = match_stage["$match"] if match_stage else {}
        results = [d for d in self.docs if self._match(d, tenant_match)]
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
        extra_match = pipeline[1] if len(pipeline) > 1 and "$match" in pipeline[1] else None
        if extra_match and extra_match["$match"].get("published") is True:
            items = [x for x in items if x["published"]]

        class _Cursor:
            def __init__(self, items):
                self.items = items

            async def to_list(self, length: int) -> list:
                return list(self.items[:length])

        return _Cursor(items)


# ── 픽스처 ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def fake_db():
    return {"scenarios": _FakeCollection()}


@pytest.fixture
def repo(fake_db):
    from app.repositories.scenario_repository import ScenarioRepository
    return ScenarioRepository(db=fake_db)


@pytest.fixture
def admin_tenant():
    from app.core.auth import TenantContext
    return TenantContext(
        tenant_id="t_acme", client_id="c_admin", roles=["admin"],
    )


@pytest.fixture
def operator_tenant():
    from app.core.auth import TenantContext
    return TenantContext(
        tenant_id="t_acme", client_id="c_op", roles=["operator"],
    )


@pytest.fixture
def client(repo, admin_tenant):
    """admin 권한으로 모든 엔드포인트 접근 가능한 기본 클라이언트."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis_client.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis_client.close_redis", new_callable=AsyncMock), \
         patch("app.core.redis_client.get_redis", return_value=AsyncMock()), \
         patch("app.domain.kill_switch.KillSwitchService.is_active",
               new_callable=AsyncMock, return_value=False):
        from app.main import app
        from app.core.auth import get_current_tenant
        from app.repositories.scenario_repository import ScenarioRepository

        app.dependency_overrides[get_current_tenant] = lambda: admin_tenant
        app.dependency_overrides[ScenarioRepository] = lambda: repo
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()


@pytest.fixture
def operator_client(repo, operator_tenant):
    """operator 권한 — publish/delete 차단 확인용."""
    with patch("app.core.database.init_db", new_callable=AsyncMock), \
         patch("app.core.database.close_db", new_callable=AsyncMock), \
         patch("app.core.redis_client.init_redis", new_callable=AsyncMock), \
         patch("app.core.redis_client.close_redis", new_callable=AsyncMock), \
         patch("app.core.redis_client.get_redis", return_value=AsyncMock()), \
         patch("app.domain.kill_switch.KillSwitchService.is_active",
               new_callable=AsyncMock, return_value=False):
        from app.main import app
        from app.core.auth import get_current_tenant
        from app.repositories.scenario_repository import ScenarioRepository

        app.dependency_overrides[get_current_tenant] = lambda: operator_tenant
        app.dependency_overrides[ScenarioRepository] = lambda: repo
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c
        app.dependency_overrides.clear()


# ── 헬퍼 ───────────────────────────────────────────────────────────────────────

def _minimal_scenario(scenario_id: str = "greet", tenant_id: str | None = None) -> dict[str, Any]:
    """통과 가능한 최소 DSL 페이로드 (1개 end 노드)."""
    body: dict[str, Any] = {
        "scenario_id": scenario_id,
        "name": "Greeting",
        "entry": "done",
        "fallback_node": None,
        "nodes": [
            {
                "id": "done",
                "type": "end",
                "config": {"closing_message": "감사합니다"},
            },
        ],
        "edges": [],
        "tags": [],
    }
    if tenant_id is not None:
        body["tenant_id"] = tenant_id
    return body


# ── 저장 ───────────────────────────────────────────────────────────────────────

def test_post_scenario_creates_with_server_versioned_and_draft(client):
    resp = client.post("/api/v1/scenarios", json=_minimal_scenario())
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["version"] == 1          # 서버 채번
    assert body["published"] is False    # 저장은 항상 draft
    assert body["tenant_id"] == "t_acme"


def test_post_scenario_second_save_increments_version(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.post("/api/v1/scenarios", json=_minimal_scenario())
    assert resp.json()["version"] == 2


def test_post_scenario_tenant_id_in_payload_is_overwritten(client, repo):
    """
    공격자가 payload 에 다른 tenant_id 를 넣어도 JWT claim 으로 강제 교체되어야 함.
    """
    body = _minimal_scenario()
    body["tenant_id"] = "t_other"  # 스푸핑 시도
    resp = client.post("/api/v1/scenarios", json=body)
    assert resp.status_code == 201
    assert resp.json()["tenant_id"] == "t_acme"
    # 실제 문서도 덮어쓴 tenant 로 저장되었는지 repo 수준에서 한번 더 확인
    fake_col = repo._db["scenarios"]
    assert all(d["tenant_id"] == "t_acme" for d in fake_col.docs)


def test_post_scenario_invalid_dsl_returns_422(client):
    """entry 가 nodes 에 없는 DSL → 422 + DSL_VALIDATION_ERROR."""
    bad = _minimal_scenario()
    bad["entry"] = "does_not_exist"
    resp = client.post("/api/v1/scenarios", json=bad)
    assert resp.status_code == 422
    detail = resp.json()["detail"]
    assert detail["code"] == "DSL_VALIDATION_ERROR"
    assert isinstance(detail["errors"], list)
    assert len(detail["errors"]) > 0


# ── 검증 (저장 없음) ───────────────────────────────────────────────────────────

def test_validate_scenario_ok(client):
    resp = client.post("/api/v1/scenarios/validate", json=_minimal_scenario())
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "issues": []}


def test_validate_scenario_not_ok(client):
    bad = _minimal_scenario()
    bad["entry"] = "missing"
    resp = client.post("/api/v1/scenarios/validate", json=bad)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert len(body["issues"]) >= 1
    first = body["issues"][0]
    assert first["severity"] == "error"
    assert "message" in first
    assert "code" in first


# ── 조회 ───────────────────────────────────────────────────────────────────────

def test_get_scenario_latest(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())  # v1
    client.post("/api/v1/scenarios", json=_minimal_scenario())  # v2
    resp = client.get("/api/v1/scenarios/greet?version=latest")
    assert resp.status_code == 200
    assert resp.json()["version"] == 2


def test_get_scenario_published_404_when_none(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.get("/api/v1/scenarios/greet?version=published")
    assert resp.status_code == 404


def test_get_scenario_by_int_version(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())  # v1
    client.post("/api/v1/scenarios", json=_minimal_scenario())  # v2
    resp = client.get("/api/v1/scenarios/greet?version=1")
    assert resp.status_code == 200
    assert resp.json()["version"] == 1


def test_get_scenario_invalid_version_string(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.get("/api/v1/scenarios/greet?version=notanumber")
    assert resp.status_code == 400


def test_get_scenario_missing_version_404(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.get("/api/v1/scenarios/greet?version=999")
    assert resp.status_code == 404


# ── 발행 (admin only) ──────────────────────────────────────────────────────────

def test_publish_scenario_admin_ok(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.post("/api/v1/scenarios/greet/publish", json={"version": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["published"] is True
    assert body["version"] == 1


def test_publish_previous_demoted(client):
    for _ in range(3):
        client.post("/api/v1/scenarios", json=_minimal_scenario())
    client.post("/api/v1/scenarios/greet/publish", json={"version": 1})
    client.post("/api/v1/scenarios/greet/publish", json={"version": 3})
    v1 = client.get("/api/v1/scenarios/greet?version=1").json()
    assert v1["published"] is False
    pub = client.get("/api/v1/scenarios/greet?version=published").json()
    assert pub["version"] == 3


def test_publish_operator_403(operator_client):
    operator_client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = operator_client.post(
        "/api/v1/scenarios/greet/publish", json={"version": 1},
    )
    assert resp.status_code == 403


def test_publish_missing_version_404(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.post(
        "/api/v1/scenarios/greet/publish", json={"version": 42},
    )
    assert resp.status_code == 404


def test_publish_bad_payload_400(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    # version 필드 누락
    resp = client.post("/api/v1/scenarios/greet/publish", json={})
    assert resp.status_code == 400
    # version 음수
    resp = client.post(
        "/api/v1/scenarios/greet/publish", json={"version": -1},
    )
    assert resp.status_code == 400


# ── 삭제 (admin only) ──────────────────────────────────────────────────────────

def test_delete_draft_version_204(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.delete("/api/v1/scenarios/greet/versions/1")
    assert resp.status_code == 204
    resp = client.get("/api/v1/scenarios/greet?version=1")
    assert resp.status_code == 404


def test_delete_published_version_409(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    client.post("/api/v1/scenarios/greet/publish", json={"version": 1})
    resp = client.delete("/api/v1/scenarios/greet/versions/1")
    assert resp.status_code == 409


def test_delete_operator_403(operator_client):
    operator_client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = operator_client.delete("/api/v1/scenarios/greet/versions/1")
    assert resp.status_code == 403


def test_delete_missing_version_404(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario())
    resp = client.delete("/api/v1/scenarios/greet/versions/999")
    assert resp.status_code == 404


# ── 목록 ───────────────────────────────────────────────────────────────────────

def test_list_scenarios_latest_per_id(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario("s1"))
    client.post("/api/v1/scenarios", json=_minimal_scenario("s1"))
    client.post("/api/v1/scenarios", json=_minimal_scenario("s2"))

    resp = client.get("/api/v1/scenarios")
    assert resp.status_code == 200
    items = resp.json()
    versions = {i["scenario_id"]: i["version"] for i in items}
    assert versions == {"s1": 2, "s2": 1}


def test_list_scenarios_include_drafts_false_excludes_unpublished(client):
    client.post("/api/v1/scenarios", json=_minimal_scenario("s1"))
    client.post("/api/v1/scenarios", json=_minimal_scenario("s2"))
    client.post("/api/v1/scenarios/s1/publish", json={"version": 1})

    resp = client.get("/api/v1/scenarios?include_drafts=false")
    assert resp.status_code == 200
    items = resp.json()
    ids = [i["scenario_id"] for i in items]
    assert ids == ["s1"]
