"""JWT authentication and tenant context middleware."""

from datetime import UTC, datetime, timedelta
from typing import Annotated, Any

import structlog
from fastapi import Depends, Request
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
    # Phase N (NG2): 토큰 issuer 격리. portal-protected route 는 require_portal_role() 가
    # issuer == "agentoe-portal" 인지 강제. 기본값은 backward-compat ("agentoe-api").
    issuer: str = "agentoe-api"


def create_access_token(
    tenant_id: str,
    client_id: str,
    roles: list[str],
    *,
    issuer: str = "agentoe-api",
    expires_minutes: int | None = None,
) -> str:
    """Create a signed JWT access token.

    Phase N (NG2): `issuer` 파라미터로 토큰 출처 식별. portal_users 발급은
    `issuer="agentoe-portal"` 로 호출 (auth_portal.py, N1.7). 기존 호출처는
    기본값 그대로 두면 backward-compat.
    """
    minutes = expires_minutes if expires_minutes is not None else settings.JWT_EXPIRE_MINUTES
    expire = datetime.now(UTC) + timedelta(minutes=minutes)
    payload = {
        "iss": issuer,
        "sub": client_id,
        "tenant_id": tenant_id,
        "roles": roles,
        "exp": expire,
        "iat": datetime.now(UTC),
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
            raise AuthorizationError("X-Tenant-Id header does not match token claim")

    return TenantContext(
        tenant_id=tenant_id,
        client_id=client_id,
        roles=payload.get("roles", []),
        # Phase N (NG2): issuer claim — portal-protected route 가 격리 검증에 사용.
        # 기본값 "agentoe-api" 는 기존 토큰 (iss claim 없음) backward-compat.
        issuer=payload.get("iss", "agentoe-api"),
    )


def require_roles(*required_roles: str) -> Any:
    """Dependency factory: require specific roles.

    issuer 검증은 하지 않음 — 기존 라우터 backward compat.
    portal-protected route 는 `require_portal_role()` 을 사용.
    """

    async def check_roles(
        tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    ) -> TenantContext:
        if not any(role in tenant.roles for role in required_roles):
            raise AuthorizationError(f"Required roles: {required_roles}")
        return tenant

    return check_roles


# ── Portal-protected RBAC (NG2) ──────────────────────────────────────────────
#
# 운영포탈 라우터 전용 가드. 자동 role 매핑 폐기 — portal:* role 은 오직
# portal_users 컬렉션에서 발급된 토큰 (iss="agentoe-portal") 에만 인정.
# 기존 admin/super_admin 토큰이 portal SSE/audit drill 권한을 우회 획득하는
# 격리 누수를 차단.

PORTAL_ISSUER: str = "agentoe-portal"
PORTAL_ROLES: frozenset[str] = frozenset(
    {
        "portal:viewer",
        "portal:operator",
        "portal:admin",
    }
)


def require_portal_role(*required_roles: str) -> Any:
    """Dependency factory: portal-issuer 토큰 + required portal:* role 중 1개.

    검증 순서:
      1) issuer == "agentoe-portal" 인지 (NG2 — 자동 매핑 폐기).
      2) tenant.roles 가 required_roles 중 하나 OR PLATFORM_ADMIN_ROLES 보유.

    PLATFORM_ADMIN_ROLES 우회는 issuer 가 portal 인 경우에만 유지 — portal_users
    컬렉션에 platform_admin 도 있을 수 있다는 drop-in 호환.
    """

    async def check_portal(
        tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    ) -> TenantContext:
        if tenant.issuer != PORTAL_ISSUER:
            logger.warning(
                "portal_route_non_portal_issuer",
                issuer=tenant.issuer,
                client_id=tenant.client_id,
                required_roles=required_roles,
            )
            raise AuthorizationError(
                f"Portal-protected endpoint requires issuer={PORTAL_ISSUER!r} token"
            )

        if any(r in PLATFORM_ADMIN_ROLES for r in tenant.roles):
            return tenant
        if any(r in tenant.roles for r in required_roles):
            return tenant

        logger.warning(
            "portal_role_denied",
            client_id=tenant.client_id,
            actual_roles=tenant.roles,
            required_roles=required_roles,
        )
        raise AuthorizationError(f"Required portal roles: {required_roles}")

    return check_portal


# ── Tenant Ownership Enforcement ─────────────────────────────────────────────
#
# 크로스테넌트 IDOR 방지용 공용 헬퍼. 라우터에서 `if resource.tenant_id != tenant_id`
# 반복 패턴을 제거한다. platform_admin 역할은 전 테넌트 접근 허용(감사로그 남김).
#
# Phase N (NG2): super_admin, portal:admin 추가. portal:admin 은 portal-issuer
# 토큰의 경우만 — assert_tenant_ownership 내부에서 issuer 검증 안 함 (라우터
# 데코레이터 require_portal_role 이 이미 portal issuer 보장). 따라서 portal:admin
# 이 PLATFORM_ADMIN_ROLES 에 들어가도 portal-protected route 통과 후에만 사용됨.

PLATFORM_ADMIN_ROLES: frozenset[str] = frozenset(
    {
        "platform_admin",
        "sre_admin",
        "super_admin",  # Phase N — 기존에 누락. plan §2.3.
        "portal:admin",  # Phase N — portal-issuer 토큰의 portal:admin.
    }
)


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
    owner = resource.get(field) if isinstance(resource, dict) else getattr(resource, field, None)

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
