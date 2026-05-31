"""Kill Switch API endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from app.core.auth import TenantContext, require_roles
from app.domain.audit_emitter import AuditEmitter, get_audit_emitter
from app.domain.kill_switch import KillSwitchScope, KillSwitchService

router = APIRouter()


@router.post("/activate", status_code=status.HTTP_200_OK)
async def activate_kill_switch(
    payload: dict,
    request: Request,
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    service: KillSwitchService = Depends(KillSwitchService),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    scope_str = payload.get("scope", "tenant")
    try:
        scope = KillSwitchScope(scope_str)
    except ValueError:
        raise HTTPException(
            400, f"Invalid scope: {scope_str}. Use: tenant, feature, scenario"
        ) from None

    result = await service.activate(
        scope=scope,
        target_id=payload["target_id"],
        reason=payload.get("reason", "Manual activation"),
        activated_by=tenant.client_id,
    )

    # Phase N (N1.3) — audit emit: kill_switch.activate
    if audit is not None:
        await audit.emit(
            action="kill_switch.activate",
            event_type="kill_switch_activate",  # 기존 query path 호환
            actor=tenant,
            resource={"type": "kill_switch", "id": f"{scope_str}:{payload['target_id']}"},
            after={
                "active": True,
                "scope": scope_str,
                "target_id": payload["target_id"],
                "reason": payload.get("reason", "Manual activation"),
            },
            request=request,
        )

    return result


@router.delete("/{switch_id}", status_code=status.HTTP_200_OK)
async def deactivate_kill_switch(
    switch_id: str,
    request: Request,
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    service: KillSwitchService = Depends(KillSwitchService),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    result = await service.deactivate(switch_id)
    if not result:
        raise HTTPException(404, f"Kill switch '{switch_id}' not found or already inactive")

    # Phase N (N1.3) — audit emit: kill_switch.deactivate
    if audit is not None:
        await audit.emit(
            action="kill_switch.deactivate",
            event_type="kill_switch_deactivate",
            actor=tenant,
            resource={"type": "kill_switch", "id": switch_id},
            before={"active": True},
            after={"active": False},
            request=request,
        )

    return result


@router.get("/status")
async def get_kill_switch_status(
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    service: KillSwitchService = Depends(KillSwitchService),
) -> dict:
    switches = await service.list_active()
    return {"active_switches": switches}
