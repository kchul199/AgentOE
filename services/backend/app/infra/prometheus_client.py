"""Prometheus HTTP API 클라이언트 (Phase N — N2.1).

N2 에서 인프라만 마련, 실 쿼리 연동은 N3 에서 고도화.

사용:
    client = get_prometheus_client()
    result = await client.query("agentoe_pipeline_calls_total")
    matrix = await client.query_range("agentoe_pipeline_latency_ms_p95", start, end, step="15s")

설정 (config.py):
    PROMETHEUS_URL  — Prometheus HTTP API base (기본: http://prometheus:9090)
    PROMETHEUS_USER — basic auth user (빈 문자열 = 인증 없음)
    PROMETHEUS_PASSWORD
"""

from __future__ import annotations

import asyncio
from typing import Any

import httpx
import structlog

logger = structlog.get_logger(__name__)

# ── 모듈 싱글톤 ──────────────────────────────────────────────────────────────
_client: PrometheusClient | None = None
_lock = asyncio.Lock()


# ── 클라이언트 ───────────────────────────────────────────────────────────────


class PrometheusClient:
    """비동기 Prometheus HTTP API 클라이언트.

    - 인스턴트 쿼리: `query(promql)` → vector (현재 시각 기준)
    - 범위 쿼리:     `query_range(promql, start, end, step)` → matrix
    - 실패 시 빈 dict/list 반환 (caller 가 fallback 처리)
    """

    def __init__(
        self,
        base_url: str,
        user: str = "",
        password: str = "",
        timeout: float = 5.0,
    ) -> None:
        auth = (user, password) if user else None
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            auth=auth,
            timeout=timeout,
            headers={"Accept": "application/json"},
        )

    # ── public API ───────────────────────────────────────────────────────────

    async def query(
        self,
        promql: str,
        at: float | None = None,
    ) -> list[dict[str, Any]]:
        """인스턴트 쿼리 → vector result list.

        반환:
            [{"metric": {...labels}, "value": [timestamp, "value"]}, ...]
        """
        params: dict[str, str] = {"query": promql}
        if at is not None:
            params["time"] = str(at)
        try:
            r = await self._http.get("/api/v1/query", params=params)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                logger.warning("prometheus_query_non_success", promql=promql, data=data)
                return []
            return data.get("data", {}).get("result", [])
        except Exception as e:
            logger.warning("prometheus_query_error", promql=promql, error=str(e))
            return []

    async def query_range(
        self,
        promql: str,
        start: float,
        end: float,
        step: str = "15s",
    ) -> list[dict[str, Any]]:
        """범위 쿼리 → matrix result list.

        반환:
            [{"metric": {...labels}, "values": [[ts, "val"], ...]}, ...]
        """
        params = {
            "query": promql,
            "start": str(start),
            "end": str(end),
            "step": step,
        }
        try:
            r = await self._http.get("/api/v1/query_range", params=params)
            r.raise_for_status()
            data = r.json()
            if data.get("status") != "success":
                logger.warning("prometheus_range_non_success", promql=promql)
                return []
            return data.get("data", {}).get("result", [])
        except Exception as e:
            logger.warning("prometheus_range_error", promql=promql, error=str(e))
            return []

    async def scalar_or_default(
        self,
        promql: str,
        default: float = 0.0,
        at: float | None = None,
    ) -> float:
        """인스턴트 쿼리에서 첫 번째 스칼라 값 반환. 실패/빈 결과 → default."""
        results = await self.query(promql, at=at)
        if not results:
            return default
        try:
            return float(results[0]["value"][1])
        except (IndexError, KeyError, ValueError):
            return default

    async def close(self) -> None:
        await self._http.aclose()


# ── 싱글톤 접근자 ─────────────────────────────────────────────────────────────


async def get_prometheus_client() -> PrometheusClient:
    """PrometheusClient 싱글톤 반환 (lazy init)."""
    global _client
    if _client is None:
        async with _lock:
            if _client is None:
                from app.core.config import settings

                _client = PrometheusClient(
                    base_url=getattr(settings, "PROMETHEUS_URL", "http://prometheus:9090"),
                    user=getattr(settings, "PROMETHEUS_USER", ""),
                    password=getattr(settings, "PROMETHEUS_PASSWORD", ""),
                )
                logger.info(
                    "prometheus_client_initialized",
                    url=getattr(settings, "PROMETHEUS_URL", "http://prometheus:9090"),
                )
    return _client


async def close_prometheus_client() -> None:
    """앱 종료 시 httpx 세션 닫기 (lifespan shutdown 에서 호출)."""
    global _client
    if _client is not None:
        await _client.close()
        _client = None
