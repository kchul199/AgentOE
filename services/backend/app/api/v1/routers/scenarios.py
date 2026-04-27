"""Scenario DSL CRUD + publish + validate endpoints.

/api/v1/scenarios/
    GET     /                                  — 테넌트의 시나리오 최신 버전 목록
    POST    /                                  — 새 버전 저장 (draft)
    GET     /{scenario_id}?version=latest|published|<int>
    POST    /{scenario_id}/publish             — 특정 버전을 published 로 전환
    POST    /validate                          — DSL 구조 검증 (저장 없이)
    DELETE  /{scenario_id}/versions/{version}  — 특정 버전 삭제 (published 거부)

인증:
    - `get_current_tenant` 의존성으로 JWT (또는 DEV X-Tenant-Id) 로부터 tenant 결정.
    - 모든 쿼리는 `tenant.tenant_id` 로 스코프 — 다른 테넌트의 시나리오 조회/쓰기 불가.
    - publish / delete 는 admin 권한 필요.

검증 파이프라인:
    payload → Scenario(**payload)  (Pydantic v2 strict, extra=forbid, graph 검증)
      → ScenarioRepository.save()  (새 버전 채번)
"""
from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import ValidationError

from app.agentic.scenario_dsl import Scenario
from app.core.auth import TenantContext, get_current_tenant, require_roles
from app.repositories.scenario_repository import (
    ScenarioConflictError,
    ScenarioNotFoundError,
    ScenarioRepository,
)

router = APIRouter()


# ── 목록 ─────────────────────────────────────────────────────────────────────

@router.get("")
async def list_scenarios(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: ScenarioRepository = Depends(ScenarioRepository),
    include_drafts: bool = Query(default=True),
) -> list[dict[str, Any]]:
    """테넌트 소유 시나리오의 최신 버전 1개씩 요약 반환."""
    return await repo.list_by_tenant(
        tenant.tenant_id, include_drafts=include_drafts,
    )


# ── 저장 (새 버전 생성) ──────────────────────────────────────────────────────

@router.post("", status_code=status.HTTP_201_CREATED)
async def save_scenario(
    payload: dict[str, Any],
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: ScenarioRepository = Depends(ScenarioRepository),
) -> dict[str, Any]:
    """
    draft 로 저장. version 필드는 서버에서 채번 — 클라이언트 값은 무시된다.
    payload.tenant_id 가 있어도 JWT claim 으로 강제 교체된다 (위변조 방지).
    """
    payload["tenant_id"] = tenant.tenant_id
    try:
        scenario = Scenario(**payload)
    except ValidationError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail={"code": "DSL_VALIDATION_ERROR", "errors": e.errors()},
        ) from e

    return await repo.save(scenario.model_dump(by_alias=True))


# ── 검증 (저장 없이) ─────────────────────────────────────────────────────────

@router.post("/validate")
async def validate_scenario(
    payload: dict[str, Any],
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> dict[str, Any]:
    """Pydantic DSL 검증만 수행. 통과 시 {ok:true, issues:[]}. 실패 시 상세."""
    payload["tenant_id"] = tenant.tenant_id
    try:
        Scenario(**payload)
    except ValidationError as e:
        return {
            "ok": False,
            "issues": [
                {
                    "severity": "error",
                    "code": ".".join(str(p) for p in err["loc"]),
                    "message": err["msg"],
                }
                for err in e.errors()
            ],
        }
    return {"ok": True, "issues": []}


# ── 조회 ─────────────────────────────────────────────────────────────────────

@router.get("/{scenario_id}")
async def get_scenario(
    scenario_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: ScenarioRepository = Depends(ScenarioRepository),
    version: str = Query(
        default="latest",
        description="정수 버전 번호, 'latest', 또는 'published'",
    ),
) -> dict[str, Any]:
    try:
        if version == "latest":
            return await repo.get_latest(tenant.tenant_id, scenario_id)
        if version == "published":
            return await repo.get_published(tenant.tenant_id, scenario_id)
        try:
            v = int(version)
        except ValueError as e:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"invalid version: {version}",
            ) from e
        return await repo.get_version(tenant.tenant_id, scenario_id, v)
    except ScenarioNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e


# ── 발행 ─────────────────────────────────────────────────────────────────────

class _PublishPayload(dict):
    """payload = {"version": int}"""


@router.post("/{scenario_id}/publish")
async def publish_scenario(
    scenario_id: str,
    payload: dict[str, Any],
    tenant: Annotated[
        TenantContext, Depends(require_roles("admin", "super_admin")),
    ],
    repo: ScenarioRepository = Depends(ScenarioRepository),
) -> dict[str, Any]:
    """version 을 published=True 로. 기존 published 버전은 자동 False."""
    version = payload.get("version")
    if not isinstance(version, int) or version < 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="payload.version (int >= 1) required",
        )
    try:
        return await repo.publish(tenant.tenant_id, scenario_id, version)
    except ScenarioNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except ScenarioConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e),
        ) from e


# ── 삭제 ─────────────────────────────────────────────────────────────────────

@router.delete(
    "/{scenario_id}/versions/{version}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_scenario_version(
    scenario_id: str,
    version: int,
    tenant: Annotated[
        TenantContext, Depends(require_roles("admin", "super_admin")),
    ],
    repo: ScenarioRepository = Depends(ScenarioRepository),
) -> None:
    try:
        await repo.delete_version(tenant.tenant_id, scenario_id, version)
    except ScenarioNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=str(e),
        ) from e
    except ScenarioConflictError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail=str(e),
        ) from e


# quelled: used for type-only annotation stability
_SCENARIO_LITERAL: Literal["latest", "published"] = "latest"
