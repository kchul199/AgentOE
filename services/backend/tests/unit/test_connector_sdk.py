"""Unit tests for BaseConnector SDK."""

import pytest

from app.connectors.base_connector import (
    BaseConnector,
    ConnectorRequest,
    ConnectorStatus,
    MaskingEngine,
)
from app.connectors.connector_registry import ConnectorRegistry


# 테스트용 구체 커넥터
class EchoConnector(BaseConnector):
    CONNECTOR_TYPE = "echo"
    ALLOWED_ACTIONS = {"echo.ping", "echo.data"}

    async def _execute(self, request: ConnectorRequest) -> dict:
        return {"echo": request.payload, "action": request.action}

    async def health_check(self) -> dict:
        return {"status": "ok"}


def make_request(action: str, payload: dict | None = None) -> ConnectorRequest:
    return ConnectorRequest(
        action=action,
        payload=payload or {},
        session_id="sess_test",
        tenant_id="tenant-test",
    )


@pytest.mark.asyncio
async def test_allowed_action_succeeds():
    conn = EchoConnector("echo-1", "t1", {})
    resp = await conn.execute(make_request("echo.ping", {"msg": "hello"}))
    assert resp.status == ConnectorStatus.OK
    assert resp.data["action"] == "echo.ping"


@pytest.mark.asyncio
async def test_blocked_action_unauthorized():
    conn = EchoConnector("echo-1", "t1", {})
    resp = await conn.execute(make_request("echo.delete"))
    assert resp.status == ConnectorStatus.UNAUTHORIZED
    assert "whitelist" in resp.error_message.lower()


@pytest.mark.asyncio
async def test_masking_engine_masks_sensitive():
    data = {
        "name": "홍길동",
        "account_number": "123456789012",
        "balance": 50000,
    }
    masked, fields = MaskingEngine.mask(data)
    assert "account_number" in fields
    assert masked["account_number"] != "123456789012"
    assert "*" in masked["account_number"]
    assert masked["name"] == "홍길동"  # 마스킹 안됨
    assert masked["balance"] == 50000  # 마스킹 안됨


@pytest.mark.asyncio
async def test_masking_short_value():
    data = {"password": "ab"}
    masked, fields = MaskingEngine.mask(data)
    assert masked["password"] == "****"
    assert "password" in fields


@pytest.mark.asyncio
async def test_connector_registry_dispatch():
    registry = ConnectorRegistry()
    registry.register(
        "echo-1",
        "crm",
        "t1",
        {
            "endpoint": "http://test-crm",
            "api_key": "test-key",
        },
    )
    connectors = registry.list_connectors("t1")
    assert "echo-1" in connectors


@pytest.mark.asyncio
async def test_registry_dispatch_unknown_connector():
    registry = ConnectorRegistry()
    resp = await registry.dispatch("t1", "unknown-conn", make_request("test"))
    assert resp.status == ConnectorStatus.ERROR
    assert "not registered" in resp.error_message
