"""
JWKS Cache — JWT 공개키 조회 캐시 + kid 회전 지원.

배경:
  - RS256/ES256 토큰 검증은 발급자의 공개키가 필요. IdP(Auth0/Keycloak/Cognito)
    는 JWKS 엔드포인트에 키 세트(kid 로 식별) 를 공개한다.
  - 모든 요청마다 JWKS HTTP 호출은 느리고 비용이 크다 → 짧은 TTL 의 메모리 캐시.
  - 키 회전 시 토큰의 kid 가 캐시에 없으면 1회 force-refresh 한 뒤 재조회.

보안 원칙:
  - JWKS_URL 이 비어 있으면 레거시 HS256(JWT_SECRET) 경로 사용.
  - 공개키 JSON 은 https 로만 받는다 (환경변수 기반 정책).
  - TTL 내 연속 실패는 백오프 (동시 폭주 방지).
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

import httpx
import structlog

from app.core.config import settings
from app.core.metrics import record_jwks_lookup, record_jwks_refresh
from app.core.timeouts import http_timeout

logger = structlog.get_logger(__name__)


@dataclass
class _CacheEntry:
    keys_by_kid: dict[str, dict[str, Any]]  # {kid: jwk_dict}
    fetched_at: float
    refreshing: bool = False


class JWKSCache:
    """프로세스 단위 싱글턴 JWKS 캐시."""

    def __init__(self) -> None:
        self._entry: _CacheEntry | None = None
        self._lock = asyncio.Lock()
        # 마지막 실패 시각 — 짧은 간격 내 재시도 방지 (30초 백오프)
        self._last_fail_at: float = 0.0
        self._fail_backoff_s: float = 30.0

    @property
    def enabled(self) -> bool:
        return bool(settings.JWKS_URL)

    async def get_key(self, kid: str | None) -> dict[str, Any] | None:
        """
        kid 로 JWK 를 조회. 캐시에 없으면 1회 force-refresh 후 재조회.
        kid=None 인 토큰은 첫 번째 키 반환 (단일 키 IdP 호환).

        메트릭 결과 분류 (Track 3):
          - hit            : 첫 캐시 조회로 매칭
          - miss           : TTL 만료 후 갱신 (단, 갱신 실패 시 stale 반환도 포함)
          - force_refresh  : kid 미스로 강제 1회 갱신 트리거 (성공/실패 무관)
          - fail           : 모든 경로 실패, None 반환
        """
        if not self.enabled:
            return None

        # 1차 캐시 조회 — _entry 가 None 이면 동기 갱신 (miss), 있으면 hit
        was_cached = self._entry is not None and (
            time.monotonic() - self._entry.fetched_at
            < settings.JWKS_CACHE_TTL_SECONDS
        )
        entry = await self._ensure_fresh()
        if entry is None:
            record_jwks_lookup("fail")
            return None

        if kid is None:
            jwk = next(iter(entry.keys_by_kid.values()), None)
            record_jwks_lookup("hit" if was_cached else "miss")
            return jwk

        jwk = entry.keys_by_kid.get(kid)
        if jwk is not None:
            record_jwks_lookup("hit" if was_cached else "miss")
            return jwk

        # kid 미스 → 즉시 1회 force-refresh 시도 (키 회전 직후일 수 있음)
        logger.info("jwks_cache.kid_miss_force_refresh", kid=kid)
        record_jwks_lookup("force_refresh")
        entry = await self._refresh(force=True)
        result = entry.keys_by_kid.get(kid) if entry else None
        if result is None:
            record_jwks_lookup("fail")
        return result

    async def _ensure_fresh(self) -> _CacheEntry | None:
        """TTL 경과 시 백그라운드 갱신. 첫 호출은 동기 갱신."""
        now = time.monotonic()
        if self._entry is None:
            return await self._refresh(force=True)

        age = now - self._entry.fetched_at
        if age < settings.JWKS_CACHE_TTL_SECONDS:
            return self._entry

        # TTL 만료 — 동기 갱신 (동시 요청은 lock 으로 단일화)
        return await self._refresh(force=False)

    async def _refresh(self, *, force: bool) -> _CacheEntry | None:
        # 직전 실패 후 백오프 중이면 캐시 유지
        now = time.monotonic()
        if not force and (now - self._last_fail_at) < self._fail_backoff_s:
            return self._entry

        async with self._lock:
            # 락 획득 후 재확인 — 다른 태스크가 이미 갱신했을 수 있음
            if self._entry and not force:
                age = time.monotonic() - self._entry.fetched_at
                if age < settings.JWKS_CACHE_TTL_SECONDS:
                    return self._entry
            # Track 3: 소요 시간 관측 — 성공/실패 레이블 분리
            start = time.monotonic()
            try:
                async with httpx.AsyncClient(timeout=http_timeout()) as client:
                    resp = await client.get(settings.JWKS_URL)
                    resp.raise_for_status()
                    data = resp.json()
                keys = data.get("keys", []) or []
                by_kid = {
                    k["kid"]: k for k in keys if "kid" in k
                }
                # kid 없는 단일 키 JWKS 도 지원 (kid=None 으로 저장)
                if not by_kid and keys:
                    by_kid = {"_default_": keys[0]}
                self._entry = _CacheEntry(
                    keys_by_kid=by_kid,
                    fetched_at=time.monotonic(),
                )
                record_jwks_refresh(time.monotonic() - start, success=True)
                logger.info(
                    "jwks_cache.refreshed",
                    url=settings.JWKS_URL,
                    kids=list(by_kid.keys()),
                )
                return self._entry
            except Exception as exc:  # noqa: BLE001
                self._last_fail_at = time.monotonic()
                record_jwks_refresh(
                    time.monotonic() - start, success=False,
                )
                logger.warning(
                    "jwks_cache.refresh_failed",
                    url=settings.JWKS_URL,
                    error=str(exc),
                    backoff_s=self._fail_backoff_s,
                )
                # 실패 시에도 stale 캐시를 반환 — 가용성 우선
                return self._entry

    def invalidate(self) -> None:
        """테스트 / 관리자 hook 용."""
        self._entry = None


jwks_cache = JWKSCache()
