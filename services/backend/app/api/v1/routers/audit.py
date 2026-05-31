"""Audit log query API."""

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from app.core.auth import TenantContext, get_current_tenant
from app.repositories.audit_repository import AuditRepository

router = APIRouter()


@router.get("/events")
async def list_audit_events(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    session_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    from_dt: datetime | None = Query(default=None),
    to_dt: datetime | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    repo: AuditRepository = Depends(AuditRepository),
) -> dict:
    items, total = await repo.query(
        tenant_id=tenant.tenant_id,
        session_id=session_id,
        event_type=event_type,
        from_dt=from_dt,
        to_dt=to_dt,
        limit=limit,
        offset=offset,
    )
    return {"items": items, "total": total, "limit": limit, "offset": offset}
