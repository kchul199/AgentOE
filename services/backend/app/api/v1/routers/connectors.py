"""Connector management API."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request, status

from app.core.auth import TenantContext, get_current_tenant, require_roles
from app.domain.audit_emitter import AuditEmitter, get_audit_emitter
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
    request: Request,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: ConnectorRepository = Depends(ConnectorRepository),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    payload["tenant_id"] = tenant.tenant_id
    doc = await repo.create(payload)

    # Phase N (N1.3) — audit emit: connector.create
    if audit is not None:
        connector_id = str(doc.get("_id", doc.get("id", "")))
        await audit.emit(
            action="connector.create",
            event_type="connector_create",
            actor=tenant,
            resource={"type": "connector", "id": connector_id},
            after={"type": payload.get("type"), "enabled": payload.get("enabled", True)},
            request=request,
        )

    return doc


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
    request: Request,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: ConnectorRepository = Depends(ConnectorRepository),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    doc = await repo.update(connector_id, payload)

    # Phase N (N1.3) — audit emit: connector.update
    if audit is not None:
        await audit.emit(
            action="connector.update",
            event_type="connector_update",
            actor=tenant,
            resource={"type": "connector", "id": connector_id},
            after={k: v for k, v in payload.items() if k not in ("tenant_id",)},
            request=request,
        )

    return doc


@router.delete("/{connector_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_connector(
    connector_id: str,
    request: Request,
    tenant: Annotated[TenantContext, Depends(require_roles("admin", "super_admin"))],
    repo: ConnectorRepository = Depends(ConnectorRepository),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> None:
    await repo.delete(connector_id)

    # Phase N (N1.3) — audit emit: connector.delete
    if audit is not None:
        await audit.emit(
            action="connector.delete",
            event_type="connector_delete",
            actor=tenant,
            resource={"type": "connector", "id": connector_id},
            before={"deleted": True},
            request=request,
        )


@router.post("/{connector_id}/test")
async def test_connector(
    connector_id: str,
    request: Request,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: ConnectorRepository = Depends(ConnectorRepository),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """커넥터 연결 테스트 (등록된 설정 기반)"""
    connector_doc = await repo.get_by_id(connector_id)

    # Phase N (N1.3) — audit emit: connector.test (운영 가시성 — 누가 테스트 했는지 추적)
    if audit is not None:
        await audit.emit(
            action="connector.test",
            event_type="connector_test",
            actor=tenant,
            resource={"type": "connector", "id": connector_id},
            request=request,
        )

    return {
        "connector_id": connector_id,
        "status": "registered",
        "type": connector_doc.get("type"),
        "enabled": connector_doc.get("enabled"),
        "note": "Full connectivity test requires runtime connector initialization",
    }
