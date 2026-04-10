"""Authentication endpoints."""
from fastapi import APIRouter
from app.core.auth import create_access_token
from app.core.exceptions import AuthenticationError

router = APIRouter()


@router.post("/token")
async def get_token(payload: dict) -> dict:
    """Issue JWT token (simplified - add real credential validation)."""
    tenant_id = payload.get("tenant_id")
    client_id = payload.get("client_id")
    client_secret = payload.get("client_secret")
    if not all([tenant_id, client_id, client_secret]):
        raise AuthenticationError("tenant_id, client_id, client_secret required")
    # TODO: validate against DB
    token = create_access_token(tenant_id, client_id, roles=["operator"])
    return {"access_token": token, "token_type": "Bearer", "expires_in": 3600, "tenant_id": tenant_id}
