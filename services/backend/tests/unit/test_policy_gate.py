"""Unit tests for Policy Gate."""

import pytest

from app.domain.policy_gate import PolicyGate, PolicyLevel


@pytest.mark.asyncio
async def test_g1_always_allowed():
    gate = PolicyGate()
    result = await gate.evaluate("account_read", PolicyLevel.G1, {})
    assert result.allowed is True


@pytest.mark.asyncio
async def test_g5_always_denied():
    gate = PolicyGate()
    result = await gate.evaluate("legal_action", PolicyLevel.G5, {})
    assert result.allowed is False
    assert result.reason == "LEGALLY_PROHIBITED"


@pytest.mark.asyncio
async def test_g3_requires_sms_auth():
    gate = PolicyGate()
    result = await gate.evaluate("account_transfer", PolicyLevel.G3, {}, session_auth_state={})
    assert result.allowed is False
    assert "sms_otp" in result.required_steps


@pytest.mark.asyncio
async def test_g3_passes_with_sms_verified():
    gate = PolicyGate()
    result = await gate.evaluate(
        "account_transfer", PolicyLevel.G3, {}, session_auth_state={"sms_verified": True}
    )
    assert result.allowed is True
