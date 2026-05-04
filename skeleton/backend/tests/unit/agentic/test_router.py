"""AgenticRouter — 4티어 롤아웃 로직 테스트.

티어 우선순위 (높→낮):
  0) AGENTIC_DISABLED (globally_disabled)
  1) override 헤더 "agentic"|"legacy"
  2) tenant_cfg {agentic_disabled | agentic_enabled}
  3) AGENTIC_TENANTS allowlist
  4) AGENTIC_CANARY_PERCENT 해시 버킷 (session_id sha256 % 100)
  5) default_legacy
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentic.router import AgenticRouter, RouteDecision


def _make_settings(**overrides) -> SimpleNamespace:
    return SimpleNamespace(
        AGENTIC_DISABLED=False,
        AGENTIC_TENANTS="",
        AGENTIC_CANARY_PERCENT=0,
        **overrides,
    )


def _make_repo(tenant_cfg: dict | None = None) -> AsyncMock:
    repo = AsyncMock()
    repo.get = AsyncMock(return_value=tenant_cfg)
    return repo


class TestKillSwitch:
    async def test_globally_disabled_overrides_everything(self) -> None:
        repo = _make_repo({"agentic_enabled": True})
        router = AgenticRouter(repo, _make_settings(AGENTIC_DISABLED=True))
        d = await router.decide(
            tenant_id="t_any", scenario_id="s", session_id="sess_1", override="agentic"
        )
        assert d == RouteDecision(False, "globally_disabled")


class TestOverrideHeader:
    async def test_override_agentic_forces_new(self) -> None:
        router = AgenticRouter(_make_repo(), _make_settings())
        d = await router.decide(
            tenant_id="t", scenario_id="s", session_id="ss", override="agentic"
        )
        assert d.use_agentic is True
        assert d.reason == "override_header"

    async def test_override_legacy_forces_old(self) -> None:
        router = AgenticRouter(
            _make_repo({"agentic_enabled": True}), _make_settings()
        )
        d = await router.decide(
            tenant_id="t", scenario_id="s", session_id="ss", override="legacy"
        )
        assert d.use_agentic is False
        assert d.reason == "override_header"


class TestTenantConfig:
    async def test_tenant_disabled_flag_wins_over_allowlist(self) -> None:
        repo = _make_repo({"agentic_disabled": True})
        router = AgenticRouter(repo, _make_settings(AGENTIC_TENANTS="t_acme"))
        d = await router.decide(
            tenant_id="t_acme", scenario_id="s", session_id="ss"
        )
        assert d.use_agentic is False
        assert d.reason == "tenant_disabled"

    async def test_tenant_enabled_with_pinned_version(self) -> None:
        repo = _make_repo({"agentic_enabled": True, "pinned_version": 7})
        router = AgenticRouter(repo, _make_settings())
        d = await router.decide(
            tenant_id="t_acme", scenario_id="s", session_id="ss"
        )
        assert d.use_agentic is True
        assert d.reason == "tenant_enabled"
        assert d.scenario_version == 7


class TestAllowlist:
    async def test_allowlist_matches(self) -> None:
        router = AgenticRouter(
            _make_repo(), _make_settings(AGENTIC_TENANTS="t_acme,t_foo")
        )
        d = await router.decide(
            tenant_id="t_foo", scenario_id="s", session_id="ss"
        )
        assert d.use_agentic is True
        assert d.reason == "tenant_allowlist"

    async def test_allowlist_empty_string_ignored(self) -> None:
        """AGENTIC_TENANTS="" 는 모두 허용하지 않아야 함."""
        router = AgenticRouter(_make_repo(), _make_settings(AGENTIC_TENANTS=""))
        d = await router.decide(
            tenant_id="", scenario_id="s", session_id="ss"
        )
        assert d.use_agentic is False

    async def test_tenant_not_in_allowlist(self) -> None:
        router = AgenticRouter(
            _make_repo(), _make_settings(AGENTIC_TENANTS="t_acme")
        )
        d = await router.decide(
            tenant_id="t_other", scenario_id="s", session_id="ss"
        )
        assert d.reason == "default_legacy"


class TestCanary:
    async def test_canary_100_always_on(self) -> None:
        router = AgenticRouter(_make_repo(), _make_settings(AGENTIC_CANARY_PERCENT=100))
        d = await router.decide(
            tenant_id="t", scenario_id="s", session_id="any_session"
        )
        assert d.use_agentic is True
        assert d.reason.startswith("canary_")

    async def test_canary_0_never_on(self) -> None:
        router = AgenticRouter(_make_repo(), _make_settings(AGENTIC_CANARY_PERCENT=0))
        d = await router.decide(
            tenant_id="t", scenario_id="s", session_id="any_session"
        )
        assert d.use_agentic is False
        assert d.reason == "default_legacy"

    async def test_canary_is_session_stable(self) -> None:
        """같은 session_id 는 항상 같은 결과."""
        router = AgenticRouter(_make_repo(), _make_settings(AGENTIC_CANARY_PERCENT=50))
        d1 = await router.decide(tenant_id="t", scenario_id="s", session_id="sess_42")
        d2 = await router.decide(tenant_id="t", scenario_id="s", session_id="sess_42")
        assert d1.use_agentic == d2.use_agentic

    async def test_canary_distribution_approx(self) -> None:
        """해시 분포가 대략 50% 근처인지 (샘플 200, 허용 편차 ±20%)."""
        router = AgenticRouter(_make_repo(), _make_settings(AGENTIC_CANARY_PERCENT=50))
        hits = 0
        samples = 200
        for i in range(samples):
            d = await router.decide(
                tenant_id="t", scenario_id="s", session_id=f"sess_{i}"
            )
            if d.use_agentic:
                hits += 1
        ratio = hits / samples
        assert 0.30 <= ratio <= 0.70, f"canary distribution off: {ratio:.2%}"


class TestDefaults:
    async def test_no_repo_no_settings_goes_legacy(self) -> None:
        router = AgenticRouter(None, _make_settings())
        d = await router.decide(
            tenant_id="t", scenario_id="s", session_id="ss"
        )
        assert d.use_agentic is False
        assert d.reason == "default_legacy"


class TestPriority:
    async def test_override_beats_tenant_cfg(self) -> None:
        """override=legacy 는 tenant_enabled 를 이긴다."""
        repo = _make_repo({"agentic_enabled": True})
        router = AgenticRouter(repo, _make_settings())
        d = await router.decide(
            tenant_id="t", scenario_id="s", session_id="ss", override="legacy"
        )
        assert d.use_agentic is False
        assert d.reason == "override_header"

    async def test_tenant_cfg_beats_canary(self) -> None:
        """tenant_enabled 는 canary 계산 없이 즉시 on."""
        repo = _make_repo({"agentic_enabled": True})
        router = AgenticRouter(
            repo, _make_settings(AGENTIC_CANARY_PERCENT=0)
        )
        d = await router.decide(
            tenant_id="t", scenario_id="s", session_id="ss"
        )
        assert d.use_agentic is True
        assert d.reason == "tenant_enabled"
