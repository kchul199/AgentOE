"""Alertmanager poller — leader election + Redis publish (plan §2.4).

설계:
  - Pod 가 N개여도 AM 은 1개 pod 만 폴링 (AM 부담 최소화).
  - Leader election: Redis SETNX `agentoe:lock:am_poller` (TTL 30s, 20s 마다 갱신).
  - 리더가 AM `/api/v2/alerts` 를 10s 마다 호출 → 변화분(추가/해소)만 Redis publish.
  - follower pod 는 lock 경쟁 중 대기 → 리더 장애 시 30s 이내 다른 pod 인수.

CLAUDE.md 규칙:
  - 모든 I/O 는 async/await, non-blocking.
  - 실패 시 예외 전파 없이 logger.warning — 메인 통화 흐름과 완전히 격리.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import socket
from typing import Any

import structlog

from app.domain.sse_broadcaster import CHANNEL_ALERTS
from app.infra.alertmanager_client import get_alertmanager_client

logger = structlog.get_logger(__name__)

# Redis key constants
_LEADER_LOCK_KEY = "agentoe:lock:am_poller"
_LOCK_TTL_S = 30  # leader TTL
_LOCK_RENEW_INTERVAL = 20  # leader 가 갱신하는 주기 (TTL 보다 짧아야)
_POLL_INTERVAL = 10  # AM 폴링 주기

# 이 pod 의 고유 식별자 (재시작 시에도 stable)
_POD_ID = socket.gethostname()


class AmPoller:
    """Alertmanager 폴링 워커.

    `start()` → lifespan startup 에서 asyncio.create_task.
    `stop()`  → lifespan shutdown.
    """

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._stop = asyncio.Event()
        self._prev_fingerprints: set[str] = set()

    async def start(self) -> None:
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="am-poller")
        logger.info("am_poller_started", pod_id=_POD_ID)

    async def stop(self) -> None:
        self._stop.set()
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3)
        logger.info("am_poller_stopped")

    # ── internal ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """리더 경쟁 루프. 리더가 되면 폴링, follower 면 대기."""
        while not self._stop.is_set():
            is_leader = await self._try_acquire_lock()
            if is_leader:
                await self._leader_loop()
            else:
                # follower — lock 만료 기다림
                logger.debug("am_poller_follower_waiting", pod_id=_POD_ID)
                await asyncio.sleep(_LOCK_RENEW_INTERVAL)

    async def _leader_loop(self) -> None:
        """리더로서 AM 폴링 + lock 갱신."""
        logger.info("am_poller_became_leader", pod_id=_POD_ID)
        elapsed = 0.0
        while not self._stop.is_set():
            # 갱신 주기마다 lock 연장
            if elapsed >= _LOCK_RENEW_INTERVAL:
                ok = await self._renew_lock()
                if not ok:
                    logger.warning("am_poller_lock_lost", pod_id=_POD_ID)
                    return  # 리더 자격 상실 → 외부 루프에서 재경쟁
                elapsed = 0.0

            await self._poll_and_publish()
            await asyncio.sleep(_POLL_INTERVAL)
            elapsed += _POLL_INTERVAL

    async def _poll_and_publish(self) -> None:
        """AM 알람 조회 → 변화분 Redis publish."""
        try:
            alerts = await get_alertmanager_client().get_alerts()
        except Exception as e:
            logger.warning("am_poller_fetch_failed", error=str(e))
            return

        # 알람 fingerprint 로 변화 감지
        current: dict[str, dict[str, Any]] = {
            _fingerprint(a): a for a in alerts if isinstance(a, dict)
        }
        current_fps = set(current.keys())

        new_fps = current_fps - self._prev_fingerprints
        resolved_fps = self._prev_fingerprints - current_fps

        redis = _get_redis()
        if redis is None:
            return

        for fp in new_fps:
            await _publish(redis, "alert.firing", current[fp])

        for fp in resolved_fps:
            # resolved 알람은 current 에 없으므로 placeholder
            await _publish(redis, "alert.resolved", {"fingerprint": fp})

        self._prev_fingerprints = current_fps

    async def _try_acquire_lock(self) -> bool:
        """Redis SETNX + EX 로 leader lock 획득 시도."""
        redis = _get_redis()
        if redis is None:
            return True  # Redis 없으면 단독 리더로 간주 (단일 pod 개발 환경)
        try:
            result = await redis.set(
                _LEADER_LOCK_KEY,
                _POD_ID,
                nx=True,  # SET if Not eXists
                ex=_LOCK_TTL_S,
            )
            return result is not None
        except Exception as e:
            logger.warning("am_poller_lock_acquire_failed", error=str(e))
            return False

    async def _renew_lock(self) -> bool:
        """기존 lock 의 TTL 연장 (이 pod 가 보유 중인 경우에만)."""
        redis = _get_redis()
        if redis is None:
            return True
        try:
            owner = await redis.get(_LEADER_LOCK_KEY)
            if owner != _POD_ID:
                return False
            await redis.expire(_LEADER_LOCK_KEY, _LOCK_TTL_S)
            return True
        except Exception as e:
            logger.warning("am_poller_lock_renew_failed", error=str(e))
            return False


# ── helpers ───────────────────────────────────────────────────────────────────


def _fingerprint(alert: dict[str, Any]) -> str:
    """알람 fingerprint — AM 의 fingerprint 필드 or labels SHA."""
    fp = alert.get("fingerprint")
    if fp:
        return str(fp)
    labels = json.dumps(alert.get("labels", {}), sort_keys=True)
    return hashlib.sha1(labels.encode(), usedforsecurity=False).hexdigest()


async def _publish(redis: Any, event: str, payload: dict[str, Any]) -> None:
    msg = json.dumps({"event": event, **payload}, default=str, ensure_ascii=False)
    try:
        await redis.publish(CHANNEL_ALERTS, msg)
    except Exception as e:
        logger.warning("am_poller_publish_failed", error=str(e), event=event)


def _get_redis() -> Any | None:
    try:
        from app.core.redis_client import get_redis

        return get_redis()
    except Exception:
        return None


# ── singleton ─────────────────────────────────────────────────────────────────

_poller: AmPoller | None = None


def init_am_poller() -> AmPoller:
    global _poller
    _poller = AmPoller()
    return _poller


def get_am_poller() -> AmPoller | None:
    return _poller
