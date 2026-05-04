"""Connector management API."""
from typing import Annotated
from fastapi import APIRouter, Depends, status
from app.core.auth import TenantContext, get_current_tenant, require_roles
from app.repositories.connector_repository import ConnectorRepository

router = APIRouter()


@router.get("/")
async def list_connectors(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: ConnectorRepository = Depends(ConnectorRepository),
) -> dict:
    items = await repo.list_by_tenant(tenant.tenant_id)
    return {"items": items, "total": len(items)}


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_connector(
    payload: dict,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: ConnectorRepository = Depends(ConnectorRepository),
) -> dict:
    payload["tenant_id"] = tenant.tenant_id
    return await repo.create(payload)


@router.get("/{connector_id}")
async def get_connector(
    connector_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: ConnectorRepository = Depends(ConnectorRepository),
) -> dict:
    return await repo.get_by_id(connector_id)


@router.put("/{connector_id}")
async def update_connector(
    connector_id: str,
    payload: dict,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: ConnectorRepository = Depends(ConnectorRepository),
) -> dict:
    return await repo.update(connector_id, payload)


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: str,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: ConnectorRepository = Depends(ConnectorRepository),
) -> None:
    await repo.delete(connector_id)


@router.post("/{connector_id}/test")
async def test_connector(
    connector_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: ConnectorRepository = Depends(ConnectorRepository),
) -> dict:
    """커넥터 연결 테스트 (등록된 설정 기반)"""
    connector_doc = await repo.get_by_id(connector_id)
    return {
        "connector_id": connector_id,
        "status": "registered",
        "type": connector_doc.get("type"),
        "enabled": connector_doc.get("enabled"),
        "note": "Full connectivity test requires runtime connector initialization",
    }
