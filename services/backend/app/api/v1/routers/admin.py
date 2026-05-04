"""Admin API — Tenant management (super_admin only)."""
from typing import Annotated
from fastapi import APIRouter, Depends, status
from app.core.auth import TenantContext, require_roles
from app.repositories.tenant_repository import TenantRepository

router = APIRouter()


@router.get("/tenants")
async def list_tenants(
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
) -> dict:
    items = await repo.list_all()
    return {"items": items, "total": len(items)}


@router.post("/tenants", status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: dict,
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
) -> dict:
    return await repo.create(payload)


@router.get("/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    caller: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    repo: TenantRepository = Depends(TenantRepository),
) -> dict:
    return await repo.get_by_id(tenant_id)


@router.patch("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    payload: dict,
    caller: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
) -> dict:
    return await repo.update(tenant_id, payload)


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: str,
    caller: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
) -> None:
    await repo.delete(tenant_id)
