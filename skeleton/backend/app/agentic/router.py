"""
Strangler Fig Router — AIPipeline(legacy) ↔ LangGraph(new) 라우팅

전환 전략:
  1) 기본값: legacy AIPipeline 계속 사용 (안전 기본)
  2) 테넌트별 feature flag 로 opt-in: settings.AGENTIC_TENANTS (쉼표 목록)
     또는 MongoDB tenant_config.agentic_enabled = True
  3) 특정 시나리오만 Agentic 사용: scenario.published && use_agentic flag
  4) 런타임 오버라이드: X-Agentic-Override 헤더 (SRE 디버깅용, 인가 필요)

안전장치:
  * Agentic 실행 중 5xx/예외 발생 → 자동으로 legacy 로 폴백 (단일 세션 내에서는 유지)
  * 테넌트 단위 kill-switch: settings.AGENTIC_DISABLED (전역 비활성화)
  * 실험군(canary) 비율 제어: AGENTIC_CANARY_PERCENT (0-100)
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import structlog

log = structlog.get_logger(__name__)


@dataclass
class RouteDecision:
    use_agentic: bool
    reason: str           # 감사/로그용
    scenario_version: int | None = None


class AgenticRouter:
    """
    테넌트 ID / 시나리오 ID / 세션 ID 를 받아 어느 엔진을 쓸지 결정.
    상태를 갖지 않으므로 싱글턴으로 써도 안전.
    """

    def __init__(self, tenant_config_repo: Any, settings: Any) -> None:
        self._repo = tenant_config_repo
        self._settings = settings

    async def decide(
        self,
        *,
        tenant_id: str,
        scenario_id: str,
        session_id: str,
        override: str | None = None,
    ) -> RouteDecision:
        # 0. 전역 kill-switch
        if getattr(self._settings, "AGENTIC_DISABLED", False):
            return RouteDecision(False, "globally_disabled")

        # 1. 수동 오버라이드 (헤더 기반)
        if override == "agentic":
            return RouteDecision(True, "override_header")
        if override == "legacy":
            return RouteDecision(False, "override_header")

        # 2. 테넌트 설정
        tenant_cfg = await self._repo.get(tenant_id) if self._repo else None
        if tenant_cfg:
            if tenant_cfg.get("agentic_disabled"):
                return RouteDecision(False, "tenant_disabled")
            if tenant_cfg.get("agentic_enabled"):
                return RouteDecision(
                    True,
                    "tenant_enabled",
                    scenario_version=tenant_cfg.get("pinned_version"),
                )

        # 3. settings 레벨 allowlist (초기 롤아웃 단계)
        allow = set(
            (getattr(self._settings, "AGENTIC_TENANTS", "") or "").split(",")
        ) - {""}
        if tenant_id in allow:
            return RouteDecision(True, "tenant_allowlist")

        # 4. 카나리(canary) 비율 — session_id 해시로 결정 (세션 내 일관성 보장)
        canary = int(getattr(self._settings, "AGENTIC_CANARY_PERCENT", 0) or 0)
        if 0 < canary <= 100:
            h = int(hashlib.sha256(session_id.encode()).hexdigest()[:8], 16)
            bucket = h % 100
            if bucket < canary:
                return RouteDecision(True, f"canary_{canary}%")

        return RouteDecision(False, "default_legacy")
