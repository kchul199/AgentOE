"""
BaseConnector SDK — AgentOE 외부 시스템 연동 추상 기반 클래스
04_외부시스템_커넥터_SDK_가이드_v7 명세 기반
"""
import asyncio
import logging
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class ConnectorStatus(str, Enum):
    OK = "ok"
    ERROR = "error"
    TIMEOUT = "timeout"
    UNAUTHORIZED = "unauthorized"
    RATE_LIMITED = "rate_limited"


@dataclass
class ConnectorRequest:
    action: str                          # e.g. "customer.read"
    payload: dict[str, Any]
    session_id: str
    tenant_id: str
    timeout_ms: int = 3000
    retry_count: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConnectorResponse:
    status: ConnectorStatus
    data: dict[str, Any]
    latency_ms: float
    connector_id: str
    error_message: str | None = None
    masked_fields: list[str] = field(default_factory=list)


class MaskingEngine:
    """민감정보 자동 마스킹 엔진"""

    SENSITIVE_KEYS = {
        "account_number", "card_number", "ssn", "password",
        "주민번호", "계좌번호", "카드번호", "비밀번호",
    }

    @classmethod
    def mask(cls, data: dict[str, Any]) -> tuple[dict[str, Any], list[str]]:
        """데이터에서 민감정보를 마스킹하고 마스킹된 필드 목록 반환"""
        masked = {}
        masked_fields = []
        for key, value in data.items():
            if key.lower() in cls.SENSITIVE_KEYS:
                if isinstance(value, str) and len(value) > 4:
                    masked[key] = value[:2] + "*" * (len(value) - 4) + value[-2:]
                else:
                    masked[key] = "****"
                masked_fields.append(key)
            elif isinstance(value, dict):
                sub_masked, sub_fields = cls.mask(value)
                masked[key] = sub_masked
                masked_fields.extend(f"{key}.{f}" for f in sub_fields)
            else:
                masked[key] = value
        return masked, masked_fields


class BaseConnector(ABC):
    """
    모든 외부 시스템 커넥터의 추상 기반 클래스.
    whitelist 기반 action 검증, 자동 마스킹, 타임아웃 처리 내장.
    """

    # 서브클래스에서 반드시 정의
    CONNECTOR_TYPE: str = "base"
    ALLOWED_ACTIONS: set[str] = set()

    def __init__(
        self,
        connector_id: str,
        tenant_id: str,
        config: dict[str, Any],
    ) -> None:
        self.connector_id = connector_id
        self.tenant_id = tenant_id
        self.config = config
        self._masking = MaskingEngine()

    async def execute(self, request: ConnectorRequest) -> ConnectorResponse:
        """
        외부 시스템 호출 진입점.
        1. Whitelist 검증
        2. 타임아웃 적용
        3. 자동 마스킹
        4. 감사 로그
        """
        # 1. Whitelist 검증
        if request.action not in self.ALLOWED_ACTIONS:
            logger.warning(
                "Connector action blocked by whitelist: action=%s connector=%s",
                request.action, self.connector_id
            )
            return ConnectorResponse(
                status=ConnectorStatus.UNAUTHORIZED,
                data={},
                latency_ms=0,
                connector_id=self.connector_id,
                error_message=f"Action '{request.action}' not in whitelist",
            )

        start = time.monotonic()
        timeout_sec = request.timeout_ms / 1000.0

        try:
            result_data = await asyncio.wait_for(
                self._execute(request),
                timeout=timeout_sec,
            )
            latency_ms = (time.monotonic() - start) * 1000

            # 3. 자동 마스킹
            masked_data, masked_fields = self._masking.mask(result_data)

            logger.info(
                "Connector call success: action=%s latency=%.0fms",
                request.action, latency_ms
            )
            return ConnectorResponse(
                status=ConnectorStatus.OK,
                data=masked_data,
                latency_ms=latency_ms,
                connector_id=self.connector_id,
                masked_fields=masked_fields,
            )

        except asyncio.TimeoutError:
            latency_ms = (time.monotonic() - start) * 1000
            logger.error(
                "Connector timeout: action=%s timeout=%.0fms",
                request.action, request.timeout_ms
            )
            return ConnectorResponse(
                status=ConnectorStatus.TIMEOUT,
                data={},
                latency_ms=latency_ms,
                connector_id=self.connector_id,
                error_message=f"Timeout after {request.timeout_ms}ms",
            )

        except Exception as exc:
            latency_ms = (time.monotonic() - start) * 1000
            logger.error("Connector error: action=%s error=%s", request.action, exc)
            return ConnectorResponse(
                status=ConnectorStatus.ERROR,
                data={},
                latency_ms=latency_ms,
                connector_id=self.connector_id,
                error_message=str(exc),
            )

    @abstractmethod
    async def _execute(self, request: ConnectorRequest) -> dict[str, Any]:
        """실제 외부 시스템 호출 — 서브클래스에서 구현"""
        ...

    @abstractmethod
    async def health_check(self) -> dict[str, Any]:
        """커넥터 연결 상태 확인"""
        ...
