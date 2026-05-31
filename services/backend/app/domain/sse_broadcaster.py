"""SSE fan-out broadcaster — Redis pub/sub → asyncio.Queue per subscriber.

설계 (plan §2.4):
  - Pod 별로 Redis SUBSCRIBE 를 1개 asyncio task 로 유지.
  - 각 SSE 핸들러는 `subscribe(channel)` 로 Queue 를 받아 async for 로 소비.
  - Pod 가 N 개여도 Redis pub/sub 가 N pod 에 동시 전달 → 모든 운영자 동일 이벤트.

채널 상수 (audit_emitter.py 와 맞춤):
  CHANNEL_AUDIT   = "agentoe:events:audit"
  CHANNEL_SESSIONS = "agentoe:events:sessions"
  CHANNEL_ALERTS  = "agentoe:events:alerts"

사용 예 (SSE 라우터):
    broadcaster = get_broadcaster()                       # app.state 에서
    queue = await broadcaster.subscribe(CHANNEL_AUDIT)
    try:
        async for msg in queue_iter(queue):
            yield f"event: audit.append\\ndata: {msg}\\n\\n"
    finally:
        await broadcaster.unsubscribe(CHANNEL_AUDIT, queue)

성능 주의:
  - Queue maxsize=200 (default). 느린 클라이언트는 이벤트 드롭 (put_nowait + log).
  - Redis SUBSCRIBE 연결은 전용 connection (일반 pool 과 분리, blocking loop 격리).
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

import structlog

logger = structlog.get_logger(__name__)

# ── 채널 상수 ─────────────────────────────────────────────────────────────────

CHANNEL_AUDIT = "agentoe:events:audit"
CHANNEL_SESSIONS = "agentoe:events:sessions"
CHANNEL_ALERTS = "agentoe:events:alerts"

ALL_CHANNELS: tuple[str, ...] = (CHANNEL_AUDIT, CHANNEL_SESSIONS, CHANNEL_ALERTS)

_QUEUE_MAXSIZE = 200


# ── SseBroadcaster ────────────────────────────────────────────────────────────


class SseBroadcaster:
    """Redis pub/sub subscriber → asyncio.Queue fan-out.

    `start()` 는 background asyncio task 로 Redis SUBSCRIBE 루프를 기동.
    `stop()` 은 lifespan shutdown 에서 호출.
    """

    def __init__(self) -> None:
        # channel → set of subscriber Queues
        self._subscribers: dict[str, set[asyncio.Queue[str | None]]] = {
            ch: set() for ch in ALL_CHANNELS
        }
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()

    # ── public API ───────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Redis SUBSCRIBE 루프를 background task 로 기동. lifespan startup 에서 호출."""
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="sse-broadcaster")
        logger.info("sse_broadcaster_started", channels=ALL_CHANNELS)

    async def stop(self) -> None:
        """Redis SUBSCRIBE 루프를 종료. lifespan shutdown 에서 호출."""
        self._stop_event.set()
        # 모든 subscriber 에게 sentinel(None) 전송 → SSE generator 종료
        for queues in self._subscribers.values():
            for q in list(queues):
                with contextlib.suppress(asyncio.QueueFull):
                    q.put_nowait(None)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(asyncio.shield(self._task), timeout=3)
        logger.info("sse_broadcaster_stopped")

    async def subscribe(self, channel: str) -> asyncio.Queue[str | None]:
        """SSE 핸들러용 Queue 를 반환. 핸들러 종료 시 `unsubscribe()` 필수."""
        q: asyncio.Queue[str | None] = asyncio.Queue(maxsize=_QUEUE_MAXSIZE)
        self._subscribers.setdefault(channel, set()).add(q)
        return q

    async def unsubscribe(self, channel: str, queue: asyncio.Queue[str | None]) -> None:
        """핸들러가 연결 해제될 때 Queue 를 제거."""
        self._subscribers.get(channel, set()).discard(queue)

    # ── internal ─────────────────────────────────────────────────────────────

    async def _run(self) -> None:
        """Redis SUBSCRIBE 메인 루프 (재연결 backoff 포함)."""
        backoff = 1.0
        while not self._stop_event.is_set():
            try:
                await self._subscribe_loop()
                backoff = 1.0  # 정상 종료 후 리셋
            except asyncio.CancelledError:
                break
            except Exception as e:
                if self._stop_event.is_set():
                    break
                logger.warning(
                    "sse_broadcaster_reconnect",
                    error=str(e),
                    backoff_s=backoff,
                )
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    async def _subscribe_loop(self) -> None:
        """Redis SUBSCRIBE 전용 connection 에서 메시지 수신 후 fan-out."""
        # 전용 connection (SUBSCRIBE 는 일반 pool 과 분리해야 함)
        import redis.asyncio as aioredis  # type: ignore

        from app.core.config import settings

        conn = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = conn.pubsub()
        await pubsub.subscribe(*ALL_CHANNELS)
        logger.info("sse_broadcaster_subscribed", channels=ALL_CHANNELS)

        try:
            async for message in pubsub.listen():
                if self._stop_event.is_set():
                    break
                if message["type"] != "message":
                    continue
                channel: str = message["channel"]
                data: str = message["data"]
                await self._fan_out(channel, data)
        finally:
            await pubsub.unsubscribe()
            await conn.aclose()

    async def _fan_out(self, channel: str, data: str) -> None:
        """수신한 메시지를 해당 채널의 모든 subscriber Queue 에 전달."""
        queues = list(self._subscribers.get(channel, set()))
        dropped = 0
        for q in queues:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dropped += 1
        if dropped:
            logger.warning(
                "sse_fan_out_queue_full",
                channel=channel,
                dropped=dropped,
                total_subscribers=len(queues),
            )


# ── Queue async generator helper ─────────────────────────────────────────────


async def queue_iter(
    queue: asyncio.Queue[str | None],
    heartbeat_interval: float = 15.0,
) -> AsyncIterator[str | None]:
    """Queue 에서 SSE payload 를 꺼내는 async generator.

    sentinel(None) 수신 시 종료. heartbeat_interval 초마다 None yield (SSE heartbeat 용).
    """
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=heartbeat_interval)
            if item is None:
                return  # sentinel — broadcaster stopped
            yield item
        except TimeoutError:
            yield None  # heartbeat tick


# ── app.state 접근 헬퍼 ──────────────────────────────────────────────────────

_broadcaster_instance: SseBroadcaster | None = None


def init_broadcaster() -> SseBroadcaster:
    """main.py lifespan startup 에서 1회 호출."""
    global _broadcaster_instance
    _broadcaster_instance = SseBroadcaster()
    return _broadcaster_instance


def get_broadcaster() -> SseBroadcaster:
    """SSE 라우터에서 broadcaster 인스턴스 획득."""
    if _broadcaster_instance is None:
        raise RuntimeError("SseBroadcaster not initialized — call init_broadcaster() first")
    return _broadcaster_instance
