"""Phase N — require_portal_role / portal-issuer 격리 unit tests (NG2 closing).

검증:
  1) agentoe-api issuer + portal:* role  → 거부 (자동 매핑 폐기)
  2) agentoe-portal issuer + portal:viewer  → 통과
  3) agentoe-portal issuer + portal:operator (viewer 요구)  → 거부 (계층은 OR 명시)
  4) agentoe-portal issuer + platform_admin  → 통과 (PLATFORM_ADMIN_ROLES 우회)
  5) PLATFORM_ADMIN_ROLES 확장 — super_admin, portal:admin 포함
"""

from __future__ import annotations

import pytest

from app.core.auth import (
    PLATFORM_ADMIN_ROLES,
    PORTAL_ISSUER,
    TenantContext,
    require_portal_role,
)
from app.core.exceptions import AuthorizationError


def _ctx(*, issuer: str, roles: list[str]) -> TenantContext:
    return TenantContext(
        tenant_id="test-tenant",
        client_id="test-client",
        roles=roles,
        issuer=issuer,
    )


@pytest.mark.asyncio
async def test_api_issuer_with_portal_role_is_rejected() -> None:
    """기존 agentoe-api 토큰에 portal:* role 이 박혀있어도 portal route 는 거부.

    NG2 — 자동 매핑 폐기. portal:* 권한은 오직 portal_users 발급 토큰에만.
    """
    dep = require_portal_role("portal:viewer")
    ctx = _ctx(issuer="agentoe-api", roles=["portal:viewer", "admin"])
    with pytest.raises(AuthorizationError, match="issuer"):
        await dep(ctx)


@pytest.mark.asyncio
async def test_portal_issuer_with_matching_role_passes() -> None:
    dep = require_portal_role("portal:viewer", "portal:operator", "portal:admin")
    ctx = _ctx(issuer=PORTAL_ISSUER, roles=["portal:viewer"])
    result = await dep(ctx)
    assert result is ctx


@pytest.mark.asyncio
async def test_portal_issuer_without_required_role_is_rejected() -> None:
    """portal:operator 토큰이 admin 전용 endpoint 호출 시 거부."""
    dep = require_portal_role("portal:admin")
    ctx = _ctx(issuer=PORTAL_ISSUER, roles=["portal:operator"])
    with pytest.raises(AuthorizationError, match="Required portal roles"):
        await dep(ctx)


@pytest.mark.asyncio
async def test_portal_issuer_with_platform_admin_passes_any_endpoint() -> None:
    """portal-issuer 토큰의 platform_admin 은 모든 portal endpoint 통과."""
    dep = require_portal_role("portal:admin")
    ctx = _ctx(issuer=PORTAL_ISSUER, roles=["platform_admin"])
    result = await dep(ctx)
    assert result is ctx


@pytest.mark.asyncio
async def test_portal_issuer_with_portal_admin_role_passes() -> None:
    """portal:admin 도 PLATFORM_ADMIN_ROLES 에 포함 — operator/viewer endpoint 통과."""
    dep = require_portal_role("portal:viewer")
    ctx = _ctx(issuer=PORTAL_ISSUER, roles=["portal:admin"])
    result = await dep(ctx)
    assert result is ctx


def test_platform_admin_roles_expanded() -> None:
    """PLATFORM_ADMIN_ROLES 가 Phase N 으로 super_admin, portal:admin 포함하도록 확장됨."""
    assert "platform_admin" in PLATFORM_ADMIN_ROLES
    assert "sre_admin" in PLATFORM_ADMIN_ROLES
    assert "super_admin" in PLATFORM_ADMIN_ROLES, "NG2 — super_admin 누락 fix"
    assert "portal:admin" in PLATFORM_ADMIN_ROLES, "NG2 — portal:admin 우회 신규"


def test_tenant_context_default_issuer_is_backward_compat() -> None:
    """기본 issuer 는 agentoe-api — 옛 토큰 (iss claim 없음) backward-compat."""
    ctx = TenantContext(tenant_id="t", client_id="c", roles=["operator"])
    assert ctx.issuer == "agentoe-api"
