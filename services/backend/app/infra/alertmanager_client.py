"""Alertmanager HTTP API 클라이언트 (plan §2.2 — N1.6 AM proxy 와 연동).

역할:
  - GET /api/v2/alerts     — 현재 firing/pending 알람 목록
  - POST /api/v2/silences  — silence 생성
  - DELETE /api/v2/silences/{id} — silence 해제

성능 규칙 (CLAUDE.md):
  - 모든 HTTP 호출은 async (httpx.AsyncClient).
  - 타임아웃 기본 5s. 실패 시 빈 리스트 반환 (graceful degradation).
  - 연결 풀은 앱 lifespan 내에서 공유 (create_client → close_client).
"""

from __future__ import annotations

from typing import Any

import httpx
import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


class AlertmanagerClient:
    """Alertmanager v2 REST 클라이언트.

    `settings.ALERTMANAGER_URL` + optional basic auth (`ALERTMANAGER_USER` / `ALERTMANAGER_PASSWORD`).
    """

    def __init__(self, client: httpx.AsyncClient | None = None) -> None:
        self._client = client  # None 이면 get_client() 로 지연 초기화

    @property
    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = _build_client()
        return self._client

    async def get_alerts(
        self, active: bool = True, inhibited: bool = False
    ) -> list[dict[str, Any]]:
        """현재 firing/pending 알람 목록 반환. 실패 시 빈 리스트."""
        url = f"{settings.ALERTMANAGER_URL}/api/v2/alerts"
        params: dict[str, str] = {
            "active": str(active).lower(),
            "inhibited": str(inhibited).lower(),
        }
        try:
            resp = await self._http.get(url, params=params, timeout=5.0)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.warning("am_get_alerts_failed", error=str(e), url=url)
            return []

    async def create_silence(self, silence: dict[str, Any]) -> dict[str, Any]:
        """silence 생성. silenceID 를 포함한 응답 반환."""
        url = f"{settings.ALERTMANAGER_URL}/api/v2/silences"
        resp = await self._http.post(url, json=silence, timeout=5.0)
        resp.raise_for_status()
        return resp.json()

    async def delete_silence(self, silence_id: str) -> None:
        """silence 해제."""
        url = f"{settings.ALERTMANAGER_URL}/api/v2/silences/{silence_id}"
        resp = await self._http.delete(url, timeout=5.0)
        resp.raise_for_status()


def _build_client() -> httpx.AsyncClient:
    auth = None
    am_user = getattr(settings, "ALERTMANAGER_USER", None)
    am_pass = getattr(settings, "ALERTMANAGER_PASSWORD", None)
    if am_user and am_pass:
        auth = httpx.BasicAuth(am_user, am_pass)
    return httpx.AsyncClient(auth=auth, timeout=10.0)


# ── module-level singleton (lifespan 주입 가능) ──────────────────────────────

_client: AlertmanagerClient | None = None


def get_alertmanager_client() -> AlertmanagerClient:
    global _client
    if _client is None:
        _client = AlertmanagerClient()
    return _client


async def close_alertmanager_client() -> None:
    global _client
    if _client is not None and _client._client is not None:
        await _client._client.aclose()
    _client = None
