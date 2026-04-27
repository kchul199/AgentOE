"""Kill Switch API endpoints."""
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from app.core.auth import TenantContext, require_roles
from app.domain.kill_switch import KillSwitchScope, KillSwitchService

router = APIRouter()


@router.post("/activate", status_code=status.HTTP_200_OK)
async def activate_kill_switch(
    payload: dict,
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    service: KillSwitchService = Depends(KillSwitchService),
) -> dict:
    scope_str = payload.get("scope", "tenant")
    try:
        scope = KillSwitchScope(scope_str)
    except ValueError:
        raise HTTPException(400, f"Invalid scope: {scope_str}. Use: tenant, feature, scenario")

    result = await service.activate(
        scope=scope,
        target_id=payload["target_id"],
        reason=payload.get("reason", "Manual activation"),
        activated_by=tenant.client_id,
    )
    return result


@router.delete("/{switch_id}", status_code=status.HTTP_200_OK)
async def deactivate_kill_switch(
    switch_id: str,
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    service: KillSwitchService = Depends(KillSwitchService),
) -> dict:
    result = await service.deactivate(switch_id)
    if not result:
        raise HTTPException(404, f"Kill switch '{switch_id}' not found or already inactive")
    return result


@router.get("/status")
async def get_kill_switch_status(
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    service: KillSwitchService = Depends(KillSwitchService),
) -> dict:
    switches = await service.list_active()
    return {"active_switches": switches}
