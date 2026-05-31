"""시나리오 관리 라우터 — 목록 / 테스트 / 배포"""
from __future__ import annotations
import random
from datetime import datetime
from fastapi import APIRouter, HTTPException

from app.models import ScenarioInfo, DeployRequest, TestRequest

router = APIRouter(prefix="/scenarios", tags=["scenarios"])

_STORE: dict[str, ScenarioInfo] = {
    "greet_v2": ScenarioInfo(
        scenario_id="greet_v2", name="인사 & 라우팅 v2", tenant_id="t_acme",
        version=5, published=True, tags=["greeting", "routing"],
        updated_at="2026-05-12T07:30:00Z", node_count=9,
        env_deployed={"dev": "v4", "staging": "v5", "prod": "v4"},
    ),
    "billing_inquiry": ScenarioInfo(
        scenario_id="billing_inquiry", name="요금 조회", tenant_id="t_acme",
        version=3, published=True, tags=["billing"],
        updated_at="2026-05-11T15:00:00Z", node_count=12,
        env_deployed={"dev": "v3", "staging": "v3", "prod": "v3"},
    ),
    "service_cancel": ScenarioInfo(
        scenario_id="service_cancel", name="해지 방어", tenant_id="t_acme",
        version=2, published=False, tags=["retention"],
        updated_at="2026-05-10T11:00:00Z", node_count=18,
        env_deployed={"dev": "v2", "staging": "v1", "prod": "v1"},
    ),
    "tech_support": ScenarioInfo(
        scenario_id="tech_support", name="기술 지원 안내", tenant_id="t_beta",
        version=1, published=True, tags=["support"],
        updated_at="2026-05-09T09:00:00Z", node_count=7,
        env_deployed={"dev": "v1", "staging": "v1", "prod": "v1"},
    ),
    "faq_general": ScenarioInfo(
        scenario_id="faq_general", name="일반 FAQ", tenant_id="t_gamma",
        version=4, published=True, tags=["faq"],
        updated_at="2026-05-08T13:00:00Z", node_count=15,
        env_deployed={"dev": "v4", "staging": "v4", "prod": "v3"},
    ),
}


@router.get("", response_model=list[ScenarioInfo])
async def list_scenarios(tenant_id: str | None = None) -> list[ScenarioInfo]:
    items = list(_STORE.values())
    if tenant_id:
        items = [s for s in items if s.tenant_id == tenant_id]
    return items


@router.get("/{scenario_id}", response_model=ScenarioInfo)
async def get_scenario(scenario_id: str) -> ScenarioInfo:
    if scenario_id not in _STORE:
        raise HTTPException(404, f"시나리오 '{scenario_id}' 없음")
    return _STORE[scenario_id]


@router.post("/{scenario_id}/test")
async def test_scenario(scenario_id: str, body: TestRequest) -> dict:
    if scenario_id not in _STORE:
        raise HTTPException(404, f"시나리오 '{scenario_id}' 없음")
    return {
        "test_id": f"test_{abs(hash(scenario_id + body.phone_number)):010d}",
        "scenario_id": scenario_id,
        "phone_number": body.phone_number,
        "status": "queued",
        "message": f"테스트 발신 예약됨 → {body.phone_number}",
        "estimated_start_s": random.randint(2, 8),
    }


@router.post("/{scenario_id}/deploy")
async def deploy_scenario(scenario_id: str, body: DeployRequest) -> dict:
    if scenario_id not in _STORE:
        raise HTTPException(404, f"시나리오 '{scenario_id}' 없음")
    sc = _STORE[scenario_id]
    version_label = f"v{sc.version}"
    # 목 배포 — env_deployed 업데이트
    new_deployed = {**sc.env_deployed, body.env: version_label}
    _STORE[scenario_id] = ScenarioInfo(
        **{**sc.model_dump(), "env_deployed": new_deployed, "updated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z"},
    )
    return {
        "scenario_id": scenario_id,
        "env": body.env,
        "version": version_label,
        "deployed_at": datetime.utcnow().isoformat(timespec="seconds") + "Z",
        "deployed_by": body.operator,
        "note": body.note,
        "status": "success",
    }
