"""Authentication endpoints."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from app.core.auth import create_access_token
from app.core.exceptions import AuthenticationError
from app.domain.audit_emitter import AuditEmitter, get_audit_emitter

router = APIRouter()


@router.post("/token")
async def get_token(
    payload: dict,
    request: Request,
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """Issue JWT token (simplified - add real credential validation)."""
    tenant_id = payload.get("tenant_id")
    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")
    if not all([tenant_id, client_id, client_secret]):
        raise AuthenticationError("tenant_id, client_id, client_secret required")
    # TODO: validate against DB
    assert isinstance(tenant_id, str) and isinstance(client_id, str)
    token = create_access_token(tenant_id, client_id, roles=["operator"])

    # Phase N (N1.3) — audit emit: auth.token_issued
    # client_secret 은 절대 after/before/details 에 포함하지 않음.
    if audit is not None:
        await audit.emit(
            action="auth.token_issued",
            event_type="auth_token_issued",
            resource={"type": "tenant", "id": tenant_id},
            after={"client_id": client_id, "roles": ["operator"]},
            request=request,
        )

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": 3600,
        "tenant_id": tenant_id,
    }
