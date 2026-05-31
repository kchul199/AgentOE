"""Policy Gate: G1~G5 risk classification and evaluation."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class PolicyLevel(StrEnum):
    G1 = "G1"  # 일반 조회 — 즉시 처리
    G2 = "G2"  # 개인정보 조회 — 로깅 필요
    G3 = "G3"  # 금융 거래 — 추가 인증 필요
    G4 = "G4"  # 고위험 작업 — 이중 승인 필요
    G5 = "G5"  # 법무 제한 — 무조건 거부


@dataclass
class PolicyEvaluationResult:
    allowed: bool
    level: PolicyLevel
    reason: str
    required_steps: list[str]


class PolicyGate:
    """Evaluates actions against policy rules."""

    LEVEL_HANDLERS = {
        PolicyLevel.G1: "_handle_g1",
        PolicyLevel.G2: "_handle_g2",
        PolicyLevel.G3: "_handle_g3",
        PolicyLevel.G4: "_handle_g4",
        PolicyLevel.G5: "_handle_g5",
    }

    async def evaluate(
        self,
        action: str,
        level: PolicyLevel,
        context: dict[str, Any],
        session_auth_state: dict[str, Any] | None = None,
    ) -> PolicyEvaluationResult:
        handler_name = self.LEVEL_HANDLERS[level]
        return await getattr(self, handler_name)(action, context, session_auth_state)

    async def _handle_g1(
        self, action: str, context: dict[str, Any], auth_state: dict[str, Any] | None
    ) -> PolicyEvaluationResult:
        return PolicyEvaluationResult(
            allowed=True, level=PolicyLevel.G1, reason="G1_PASS", required_steps=[]
        )

    async def _handle_g2(
        self, action: str, context: dict[str, Any], auth_state: dict[str, Any] | None
    ) -> PolicyEvaluationResult:
        # G2: allowed but must be logged
        return PolicyEvaluationResult(
            allowed=True,
            level=PolicyLevel.G2,
            reason="G2_LOG_REQUIRED",
            required_steps=["audit_log"],
        )

    async def _handle_g3(
        self, action: str, context: dict[str, Any], auth_state: dict[str, Any] | None
    ) -> PolicyEvaluationResult:
        if auth_state and auth_state.get("sms_verified"):
            return PolicyEvaluationResult(
                allowed=True, level=PolicyLevel.G3, reason="G3_AUTH_VERIFIED", required_steps=[]
            )
        return PolicyEvaluationResult(
            allowed=False, level=PolicyLevel.G3, reason="AUTH_REQUIRED", required_steps=["sms_otp"]
        )

    async def _handle_g4(
        self, action: str, context: dict[str, Any], auth_state: dict[str, Any] | None
    ) -> PolicyEvaluationResult:
        if auth_state and auth_state.get("dual_approved"):
            return PolicyEvaluationResult(
                allowed=True, level=PolicyLevel.G4, reason="G4_DUAL_APPROVED", required_steps=[]
            )
        return PolicyEvaluationResult(
            allowed=False,
            level=PolicyLevel.G4,
            reason="DUAL_APPROVAL_REQUIRED",
            required_steps=["sms_otp", "supervisor_approval"],
        )

    async def _handle_g5(
        self, action: str, context: dict[str, Any], auth_state: dict[str, Any] | None
    ) -> PolicyEvaluationResult:
        return PolicyEvaluationResult(
            allowed=False, level=PolicyLevel.G5, reason="LEGALLY_PROHIBITED", required_steps=[]
        )
