"""
ConnectorRegistry — 테넌트별 커넥터 인스턴스 관리
세션 스코프 자격증명, whitelist 거버넌스
"""

import logging
from typing import Any

from app.connectors.base_connector import BaseConnector, ConnectorRequest, ConnectorResponse
from app.connectors.crm_connector import CRMConnector

logger = logging.getLogger(__name__)

CONNECTOR_TYPE_MAP: dict[str, type[BaseConnector]] = {
    "crm": CRMConnector,
}


class ConnectorRegistry:
    """
    테넌트별 커넥터 인스턴스 캐시.
    DB에서 커넥터 설정 로드 → 인스턴스 생성 → 캐시 관리.
    """

    def __init__(self) -> None:
        self._cache: dict[str, BaseConnector] = {}

    def _make_key(self, tenant_id: str, connector_id: str) -> str:
        return f"{tenant_id}:{connector_id}"

    def register(
        self,
        connector_id: str,
        connector_type: str,
        tenant_id: str,
        config: dict[str, Any],
    ) -> BaseConnector:
        """커넥터 인스턴스 등록"""
        cls = CONNECTOR_TYPE_MAP.get(connector_type)
        if cls is None:
            raise ValueError(f"Unknown connector type: '{connector_type}'")
        instance = cls(connector_id=connector_id, tenant_id=tenant_id, config=config)
        self._cache[self._make_key(tenant_id, connector_id)] = instance
        logger.info(
            "Connector registered: id=%s type=%s tenant=%s", connector_id, connector_type, tenant_id
        )
        return instance

    def get(self, tenant_id: str, connector_id: str) -> BaseConnector | None:
        return self._cache.get(self._make_key(tenant_id, connector_id))

    async def dispatch(
        self, tenant_id: str, connector_id: str, request: ConnectorRequest
    ) -> ConnectorResponse:
        """커넥터 조회 후 실행"""
        connector = self.get(tenant_id, connector_id)
        if connector is None:
            from app.connectors.base_connector import ConnectorStatus

            return ConnectorResponse(
                status=ConnectorStatus.ERROR,
                data={},
                latency_ms=0,
                connector_id=connector_id,
                error_message=f"Connector '{connector_id}' not registered for tenant '{tenant_id}'",
            )
        return await connector.execute(request)

    def list_connectors(self, tenant_id: str) -> list[str]:
        prefix = f"{tenant_id}:"
        return [k[len(prefix) :] for k in self._cache if k.startswith(prefix)]


# 전역 레지스트리 싱글턴
_registry = ConnectorRegistry()


def get_registry() -> ConnectorRegistry:
    return _registry
