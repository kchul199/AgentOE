"""
CRM Connector — HTTP REST 기반 CRM 시스템 연동
고객 정보 조회/갱신, 통화 이력 저장
"""
import logging
from typing import Any

from app.connectors.base_connector import BaseConnector, ConnectorRequest

logger = logging.getLogger(__name__)


class CRMConnector(BaseConnector):
    """
    HTTP REST CRM 커넥터.
    config 필수 키: endpoint, api_key
    """

    CONNECTOR_TYPE = "crm"
    ALLOWED_ACTIONS = {
        "customer.read",
        "customer.update",
        "call_history.write",
        "product.read",
    }

    def __init__(self, connector_id: str, tenant_id: str, config: dict[str, Any]) -> None:
        super().__init__(connector_id, tenant_id, config)
        self._session: Any = None

    def _get_session(self) -> Any:
        """httpx 세션 지연 초기화"""
        if self._session is None:
            try:
                import httpx
                self._session = httpx.AsyncClient(
                    base_url=self.config["endpoint"],
                    headers={
                        "Authorization": f"Bearer {self.config['api_key']}",
                        "Content-Type": "application/json",
                        "X-Tenant-ID": self.tenant_id,
                    },
                    timeout=5.0,
                )
            except ImportError:
                raise RuntimeError("httpx not installed")
        return self._session

    async def _execute(self, request: ConnectorRequest) -> dict[str, Any]:
        session = self._get_session()
        action = request.action
        payload = request.payload

        if action == "customer.read":
            customer_id = payload.get("customer_id") or payload.get("phone_number")
            response = await session.get(f"/customers/{customer_id}")
            response.raise_for_status()
            return response.json()

        elif action == "customer.update":
            customer_id = payload["customer_id"]
            response = await session.patch(
                f"/customers/{customer_id}",
                json={k: v for k, v in payload.items() if k != "customer_id"},
            )
            response.raise_for_status()
            return response.json()

        elif action == "call_history.write":
            response = await session.post("/call-history", json=payload)
            response.raise_for_status()
            return {"created": True, "id": response.json().get("id")}

        elif action == "product.read":
            product_id = payload.get("product_id", "")
            response = await session.get(f"/products/{product_id}")
            response.raise_for_status()
            return response.json()

        raise ValueError(f"Unexpected action: {action}")

    async def health_check(self) -> dict[str, Any]:
        import time
        start = time.monotonic()
        try:
            session = self._get_session()
            resp = await session.get("/health")
            latency_ms = (time.monotonic() - start) * 1000
            return {
                "status": "ok" if resp.status_code == 200 else "error",
                "latency_ms": round(latency_ms, 1),
                "endpoint": self.config.get("endpoint"),
            }
        except Exception as exc:
            return {"status": "error", "error": str(exc)}
