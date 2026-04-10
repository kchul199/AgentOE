"""Session management API endpoints."""
from typing import Annotated
import uuid

from fastapi import APIRouter, Depends, Query, status

from app.core.auth import TenantContext, get_current_tenant
from app.core.exceptions import SessionStateError
from app.domain.session_fsm import SessionFSM, SessionState
from app.repositories.session_repository import SessionRepository

router = APIRouter()


@router.post("/", status_code=status.HTTP_201_CREATED)
async def create_session(
    payload: dict,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: SessionRepository = Depends(SessionRepository),
) -> dict:
    session_id = f"sess_{uuid.uuid4()}"
    session_data = {
        "session_id": session_id,
        "tenant_id": tenant.tenant_id,
        "scenario_id": payload.get("scenario_id", "default"),
        "status": SessionState.IDLE,
        "caller_number": payload.get("caller_number"),
        "metadata": payload.get("metadata", {}),
    }
    created = await repo.create(session_data)
    return {**created, "ws_url": f"wss://api.agentoe.io/ws/sessions/{session_id}"}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: SessionRepository = Depends(SessionRepository),
) -> dict:
    session = await repo.get_by_id(session_id)
    if session["tenant_id"] != tenant.tenant_id:
        from app.core.exceptions import AuthorizationError
        raise AuthorizationError("Access denied")
    return session


@router.get("/")
async def list_sessions(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    status_filter: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    repo: SessionRepository = Depends(SessionRepository),
) -> dict:
    items, total = await repo.list_by_tenant(tenant.tenant_id, status_filter, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(
    session_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: SessionRepository = Depends(SessionRepository),
) -> None:
    session = await repo.get_by_id(session_id)
    if session["tenant_id"] != tenant.tenant_id:
        from app.core.exceptions import AuthorizationError
        raise AuthorizationError("Access denied")
    await repo.end_session(session_id)
