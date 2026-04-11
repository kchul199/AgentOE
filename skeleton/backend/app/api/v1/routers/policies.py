"""Policy Gate CRUD + evaluate API."""
import uuid
from typing import Annotated
from fastapi import APIRouter, Depends, status
from app.core.auth import TenantContext, get_current_tenant, require_roles
from app.domain.policy_gate import PolicyGate, PolicyLevel
from app.repositories.policy_repository import PolicyRepository

router = APIRouter()


@router.get("/")
async def list_policies(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: PolicyRepository = Depends(PolicyRepository),
) -> dict:
    items = await repo.list_by_tenant(tenant.tenant_id)
    return {"items": items, "total": len(items)}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_policy(
    payload: dict,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: PolicyRepository = Depends(PolicyRepository),
) -> dict:
    payload["policy_id"] = f"pol_{uuid.uuid4().hex[:12]}"
    payload["tenant_id"] = tenant.tenant_id
    return await repo.create(payload)


@router.get("/{policy_id}")
async def get_policy(
    policy_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: PolicyRepository = Depends(PolicyRepository),
) -> dict:
    return await repo.get_by_id(policy_id)


@router.put("/{policy_id}")
async def update_policy(
    policy_id: str,
    payload: dict,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: PolicyRepository = Depends(PolicyRepository),
) -> dict:
    return await repo.update(policy_id, payload)


@router.delete("/{policy_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_policy(
    policy_id: str,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: PolicyRepository = Depends(PolicyRepository),
) -> None:
    await repo.delete(policy_id)


@router.post("/evaluate")
async def evaluate_policy(
    payload: dict,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
) -> dict:
    """Policy Gate 즉시 평가"""
    gate = PolicyGate()
    try:
        level = PolicyLevel(payload.get("level", "G1"))
    except ValueError:
        level = PolicyLevel.G1
    result = await gate.evaluate(
        action=payload.get("action", "unknown"),
        level=level,
        context=payload.get("context", {}),
        session_auth_state=payload.get("auth_state"),
    )
    return {
        "allowed": result.allowed,
        "level": result.level.value,
        "reason": result.reason,
        "required_steps": result.required_steps,
    }
