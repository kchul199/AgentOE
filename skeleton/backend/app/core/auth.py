"""JWT authentication and tenant context middleware."""
from datetime import datetime, timedelta, timezone
from typing import Annotated

import structlog
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError

logger = structlog.get_logger()
security = HTTPBearer()


class TenantContext(BaseModel):
    tenant_id: str
    client_id: str
    roles: list[str] = []


def create_access_token(tenant_id: str, client_id: str, roles: list[str]) -> str:
    """Create a signed JWT access token."""
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.JWT_EXPIRE_MINUTES)
    payload = {
        "sub": client_id,
        "tenant_id": tenant_id,
        "roles": roles,
        "exp": expire,
        "iat": datetime.now(timezone.utc),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


async def get_current_tenant(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> TenantContext:
    """Dependency: decode JWT and return TenantContext."""
    try:
        payload = jwt.decode(
            credentials.credentials,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        return TenantContext(
            tenant_id=payload["tenant_id"],
            client_id=payload["sub"],
            roles=payload.get("roles", []),
        )
    except JWTError as e:
        raise AuthenticationError(f"Invalid token: {e}") from e


def require_roles(*required_roles: str):
    """Dependency factory: require specific roles."""
    async def check_roles(tenant: Annotated[TenantContext, Depends(get_current_tenant)]) -> TenantContext:
        if not any(role in tenant.roles for role in required_roles):
            raise AuthorizationError(f"Required roles: {required_roles}")
        return tenant
    return check_roles
