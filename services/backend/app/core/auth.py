"""JWT authentication and tenant context middleware."""
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

import structlog
from fastapi import Depends, Header, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError, jwt
from pydantic import BaseModel

from app.core.config import settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.core.jwks_cache import jwks_cache

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


async def _decode_with_jwks(token: str) -> dict[str, Any]:
    """RS256/ES256 토큰을 JWKS 캐시에서 kid 로 키 조회 후 검증."""
    try:
        header = jwt.get_unverified_header(token)
    except JWTError as e:
        raise AuthenticationError(f"Malformed token header: {e}") from e

    kid = header.get("kid")
    jwk = await jwks_cache.get_key(kid)
    if jwk is None:
        raise AuthenticationError(f"Unknown signing key (kid={kid})")

    # iss / aud 옵션 활성 시 함께 검증
    options: dict[str, Any] = {}
    if settings.JWT_AUDIENCE:
        options["audience"] = settings.JWT_AUDIENCE
    if settings.JWT_ISSUER:
        options["issuer"] = settings.JWT_ISSUER

    # python-jose 는 JWK dict 를 알고리즘 추론 + 검증에 직접 사용 가능
    algorithms = [header.get("alg")] if header.get("alg") else None
    try:
        return jwt.decode(token, jwk, algorithms=algorithms, **options)
    except JWTError as e:
        raise AuthenticationError(f"Invalid token: {e}") from e


async def _decode_legacy_hs(token: str) -> dict[str, Any]:
    """레거시 HS256 대칭키 경로 (JWT_SECRET)."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
    except JWTError as e:
        raise AuthenticationError(f"Invalid token: {e}") from e


async def get_current_tenant(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(security)],
) -> TenantContext:
    """Dependency: decode JWT and return TenantContext.

    경로 선택:
      - settings.JWKS_URL 이 설정되어 있으면 JWKS 캐시 경로 (kid 기반 RS/ES)
      - 아니면 기존 HS256 대칭키 경로 (JWT_SECRET)

    ENFORCE_TENANT_HEADER_MATCH=True 인 경우, X-Tenant-Id 헤더가 있으면
    JWT claim.tenant_id 와 일치해야 함 (위변조 방지).
    """
    token = credentials.credentials

    if jwks_cache.enabled:
        payload = await _decode_with_jwks(token)
    else:
        payload = await _decode_legacy_hs(token)

    tenant_id = payload.get("tenant_id")
    client_id = payload.get("sub")
    if not tenant_id or not client_id:
        raise AuthenticationError("Missing tenant_id or sub in token")

    # X-Tenant-Id 교차검증 (위변조 차단)
    if settings.ENFORCE_TENANT_HEADER_MATCH:
        header_tid = request.headers.get("X-Tenant-Id")
        if header_tid and header_tid != tenant_id:
            logger.warning(
                "tenant_header_mismatch",
                header_tenant=header_tid,
                jwt_tenant=tenant_id,
                client_id=client_id,
                path=request.url.path,
            )
            raise AuthorizationError(
                "X-Tenant-Id header does not match token claim"
            )

    return TenantContext(
        tenant_id=tenant_id,
        client_id=client_id,
        roles=payload.get("roles", []),
    )


def require_roles(*required_roles: str):
    """Dependency factory: require specific roles."""
    async def check_roles(tenant: Annotated[TenantContext, Depends(get_current_tenant)]) -> TenantContext:
        if not any(role in tenant.roles for role in required_roles):
            raise AuthorizationError(f"Required roles: {required_roles}")
        return tenant
    return check_roles


# ── Tenant Ownership Enforcement ─────────────────────────────────────────────
#
# 크로스테넌트 IDOR 방지용 공용 헬퍼. 라우터에서 `if resource.tenant_id != tenant_id`
# 반복 패턴을 제거한다. platform_admin 역할은 전 테넌트 접근 허용(감사로그 남김).

PLATFORM_ADMIN_ROLES: frozenset[str] = frozenset({"platform_admin", "sre_admin"})


def assert_tenant_ownership(
    resource: dict | object,
    tenant: TenantContext,
    *,
    field: str = "tenant_id",
    resource_type: str = "resource",
    resource_id: str | None = None,
) -> None:
    """
    리소스가 현재 테넌트 소속인지 검증. 플랫폼 관리자는 우회 허용(감사 로그 기록).

    Raises:
        AuthorizationError: 테넌트 불일치 시.

    사용 예::

        session = await repo.get_by_id(session_id)
        assert_tenant_ownership(session, tenant, resource_type="session", resource_id=session_id)
    """
    if isinstance(resource, dict):
        owner = resource.get(field)
    else:
        owner = getattr(resource, field, None)

    if owner is None:
        # 명시적 fail-closed: 리소스에 tenant_id 없으면 거부
        raise AuthorizationError(f"{resource_type} missing tenant scope")

    if owner == tenant.tenant_id:
        return

    if any(r in PLATFORM_ADMIN_ROLES for r in tenant.roles):
        logger.warning(
            "cross_tenant_access_by_platform_admin",
            resource_type=resource_type,
            resource_id=resource_id,
            actor_tenant=tenant.tenant_id,
            target_tenant=owner,
            client_id=tenant.client_id,
        )
        return

    logger.warning(
        "cross_tenant_access_denied",
        resource_type=resource_type,
        resource_id=resource_id,
        actor_tenant=tenant.tenant_id,
        target_tenant=owner,
        client_id=tenant.client_id,
    )
    raise AuthorizationError(f"Access denied to {resource_type}")
