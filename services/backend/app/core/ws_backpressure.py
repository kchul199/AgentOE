"""
WebSocket back-pressure 제어.

배경:
  느린 클라이언트(모바일 네트워크 열악, 브라우저 탭 숨김 등) 가 WebSocket 수신을 따라
  오지 못하면 서버 측 asyncio 송신 버퍼가 무한히 커져 메모리 압박 + GC 스톨 + 심한 경우
  OOM 으로 이어진다. 이는 한 세션이 다른 세션들을 말아먹는 noisy-neighbor 문제.

전략:
  세션 당 송신 큐(asyncio.Queue) 를 고정 크기(maxsize) 로 두고, 가득 차면
  "오래된 오디오 청크를 drop" 한다. 오디오는 실시간성이 중요하고 1초 지연된
  오디오는 어차피 쓸모없기 때문 — 최근 것만 살리는 편이 UX 에 가까움.

Drop 정책:
  - audio 이벤트(tts_ready 등) : drop-oldest  — 가장 오래된 audio 하나 빼고 push
  - 비-audio 이벤트(state/text) : drop-newest  — 기존 큐 유지, 이번 이벤트만 drop
    (상태 전이/텍스트는 순서 보존이 중요 — 유실이 덜 해로운 건 최신 것)

메트릭:
  agentoe_ws_send_queue_depth{tenant}  — 현재 큐 점유율(게이지; 인스턴스당).
  agentoe_ws_drops_total{tenant, kind} — 누적 drop 수 (kind ∈ {audio, event, full}).

한계:
  Uvicorn/starlette 의 WebSocket.send_* 는 이미 내부 asyncio write buffer 가 있다.
  이 모듈은 그 위에 "명시적" 큐를 둬서 우리가 drop 정책을 통제하도록 한다.
  완벽하지 않지만 테스트 가능하고 메트릭화 가능한 것이 핵심.
"""

from __future__ import annotations

import asyncio
import logging
from collections import deque
from dataclasses import dataclass, field

from app.core.metrics import (
    record_ws_drop,
    set_ws_queue_depth,
)

logger = logging.getLogger(__name__)

# 기본 큐 크기. 1 이벤트 ≈ 50ms 의 오디오 라고 가정하면 64 = 약 3.2 초 buffer.
# 이보다 크면 느린 클라에 의한 지연이 UX에 느껴짐. 3초 이상 끊긴 통화는 어차피 장애.
DEFAULT_MAX_QUEUE_SIZE = 64

# audio 류 이벤트 이름 (drop-oldest 대상)
# call_session_orchestrator.OutboundEvent.name 기준.
_AUDIO_EVENT_NAMES = frozenset({"tts_ready"})


@dataclass
class QueuedEvent:
    """큐에 들어가는 단위. 직렬화된 문자열만 보유."""

    name: str
    payload: str  # 이미 JSON 직렬화된 상태


@dataclass
class BoundedWSSender:
    """
    세션당 하나. WebSocket send 를 백그라운드 task 로 돌려 송신 버퍼를 격리.

    사용:
        sender = BoundedWSSender(ws, tenant_id="t_acme", max_queue_size=64)
        await sender.start()
        sender.enqueue("tts_ready", json_str)   # non-blocking
        ...
        await sender.close()
    """

    ws: object  # fastapi.WebSocket 또는 테스트 스텁
    tenant_id: str
    max_queue_size: int = DEFAULT_MAX_QUEUE_SIZE
    _queue: deque[QueuedEvent] = field(default_factory=deque)
    _condition: asyncio.Condition = field(default_factory=asyncio.Condition)
    _task: asyncio.Task[None] | None = None
    _closed: bool = False
    _wakeup: asyncio.Future[None] | None = None

    async def start(self) -> None:
        if self._task is None:
            self._task = asyncio.create_task(self._drain_loop())

    def enqueue(self, name: str, payload: str) -> bool:
        """
        큐에 이벤트 추가. 반환값은 accepted(True) / dropped(False).

        이 메서드는 **블록되지 않음** — 가득 차면 drop 하고 카운터 증가.
        awaitable 이 아니므로 호출 경로에서 await 불필요.
        """
        if self._closed:
            return False

        if len(self._queue) < self.max_queue_size:
            self._queue.append(QueuedEvent(name, payload))
            self._notify()
            set_ws_queue_depth(self.tenant_id, len(self._queue))
            return True

        # 큐 가득참 — drop 정책 적용
        if name in _AUDIO_EVENT_NAMES:
            # drop-oldest audio: 가장 오래된 audio 1개 제거 후 새 event 추가
            dropped_any = False
            for idx, evt in enumerate(self._queue):
                if evt.name in _AUDIO_EVENT_NAMES:
                    del self._queue[idx]
                    dropped_any = True
                    record_ws_drop(self.tenant_id, "audio")
                    break
            if dropped_any:
                self._queue.append(QueuedEvent(name, payload))
                self._notify()
                set_ws_queue_depth(self.tenant_id, len(self._queue))
                return True
            # 오디오 drop 대상이 없으면 full 로 처리(방어 — 실제로는 드묾)
            record_ws_drop(self.tenant_id, "full")
            return False

        # 비-audio 이벤트는 drop-newest (큐 유지, 이번 이벤트 버림)
        record_ws_drop(self.tenant_id, "event")
        return False

    def _notify(self) -> None:
        # 간단 signal — 수신 대기중인 drain loop 를 깨움.
        # Condition 사용으로 race-free 보장.
        fut = getattr(self, "_wakeup", None)
        if fut is not None and not fut.done():
            fut.set_result(None)

    async def _drain_loop(self) -> None:
        """
        큐를 순차적으로 비우며 WebSocket 으로 실제 송신.

        send 가 실패하면(클라이언트 이미 연결 해제 등) 루프 종료 — 이 sender 는
        더 이상 유효하지 않으며 상위 endpoint 가 세션을 정리해야 함.
        """
        send = getattr(self.ws, "send_text", None)
        if send is None:
            logger.error("ws object has no send_text — sender cannot start")
            return

        loop = asyncio.get_running_loop()
        while True:
            if not self._queue:
                # 큐가 비어있고 close 요청이면 루프 종료 (잔여 이벤트 없음)
                if self._closed:
                    break
                # 대기
                self._wakeup = loop.create_future()
                try:
                    # 짧은 timeout 으로 주기적 체크 — close 신호 놓치지 않게.
                    await asyncio.wait_for(self._wakeup, timeout=1.0)
                except TimeoutError:
                    continue
                finally:
                    self._wakeup = None
                continue

            # closed 여부와 무관하게 큐 잔여 이벤트는 flush (hangup 등 마지막 이벤트 보장)
            evt = self._queue.popleft()
            set_ws_queue_depth(self.tenant_id, len(self._queue))
            try:
                await send(evt.payload)
            except Exception as exc:
                # 클라이언트 연결 해제 / 네트워크 오류. 이후 enqueue 모두 drop 되도록.
                logger.debug(
                    "ws send failed (name=%s tenant=%s): %s",
                    evt.name,
                    self.tenant_id,
                    exc,
                )
                self._closed = True
                break

    async def close(self) -> None:
        """
        drain loop 종료. 큐에 남은 이벤트는 drop 되지 않고 그대로 유실될 수 있음
        (WebSocket 자체가 닫히므로 의미 없음). 게이지는 0 으로.
        """
        self._closed = True
        self._notify()
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=1.0)
            except (TimeoutError, asyncio.CancelledError):
                self._task.cancel()
        set_ws_queue_depth(self.tenant_id, 0)

    # 편의: 상위에서 테스트 가능하도록 스냅샷 노출
    @property
    def queue_depth(self) -> int:
        return len(self._queue)

    @property
    def is_closed(self) -> bool:
        return self._closed
