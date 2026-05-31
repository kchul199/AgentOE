"""Unit tests for WebSocket back-pressure (BoundedWSSender).

검증 포인트:
  - enqueue 가 non-blocking 이며 큐 여유 있을 때 accept 한다
  - drop-oldest audio: 큐 가득 찼을 때 오래된 audio 1개를 버리고 새 audio push
  - drop-newest 비-audio: 큐 가득 찼을 때 이번 이벤트를 버림 (큐 유지)
  - drain_loop: enqueue 된 이벤트가 실제 ws.send_text 로 순서대로 flush
  - close: drain task 종료, 추가 enqueue 는 false 반환
  - send 실패 시 sender._closed = True 전이
  - 메트릭: record_ws_drop / set_ws_queue_depth 호출 여부
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from app.core.ws_backpressure import (
    _AUDIO_EVENT_NAMES,
    BoundedWSSender,
    QueuedEvent,
)


class _StubWS:
    """ws.send_text 만 갖춘 최소 스텁. 호출 기록을 보관."""

    def __init__(self, *, fail_after: int | None = None) -> None:
        self.sent: list[str] = []
        self._fail_after = fail_after  # N번째(0-base) send 부터 실패
        self._n = 0

    async def send_text(self, payload: str) -> None:
        if self._fail_after is not None and self._n >= self._fail_after:
            self._n += 1
            raise ConnectionError("simulated disconnect")
        self.sent.append(payload)
        self._n += 1


# ── 기본 enqueue/drain ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_enqueue_accepts_when_queue_has_room():
    ws = _StubWS()
    sender = BoundedWSSender(ws=ws, tenant_id="t1", max_queue_size=3)
    await sender.start()

    assert sender.enqueue("state_change", '{"a":1}') is True
    assert sender.enqueue("state_change", '{"a":2}') is True

    # drain loop 에 시간 주기
    await asyncio.sleep(0.05)
    await sender.close()

    assert ws.sent == ['{"a":1}', '{"a":2}']


@pytest.mark.asyncio
async def test_enqueue_drains_in_fifo_order():
    ws = _StubWS()
    sender = BoundedWSSender(ws=ws, tenant_id="t1", max_queue_size=10)
    await sender.start()

    for i in range(5):
        sender.enqueue("state_change", f"p{i}")

    await asyncio.sleep(0.1)
    await sender.close()
    assert ws.sent == ["p0", "p1", "p2", "p3", "p4"]


# ── drop 정책 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_drop_oldest_audio_when_full():
    """큐가 오디오로 가득하면 가장 오래된 audio 를 drop 하고 새 audio 를 push."""
    ws = _StubWS()
    # drain loop 가 돌지 않도록 start() 호출 생략 + 직접 큐 제어
    sender = BoundedWSSender(ws=ws, tenant_id="t1", max_queue_size=2)

    # 큐를 audio 2개로 채움
    assert sender.enqueue("tts_ready", "old1") is True
    assert sender.enqueue("tts_ready", "old2") is True
    assert sender.queue_depth == 2

    # 3번째 audio: drop-oldest 로 "old1" 제거, "new" 추가
    with patch("app.core.ws_backpressure.record_ws_drop") as rec:
        assert sender.enqueue("tts_ready", "new") is True
        rec.assert_called_once_with("t1", "audio")

    # "old1" 이 사라지고, "old2" + "new" 만 남음
    payloads = [e.payload for e in sender._queue]
    assert payloads == ["old2", "new"]


@pytest.mark.asyncio
async def test_drop_newest_non_audio_when_full():
    """큐가 가득할 때 비-audio 이벤트는 이번 이벤트만 drop."""
    ws = _StubWS()
    sender = BoundedWSSender(ws=ws, tenant_id="t2", max_queue_size=2)

    assert sender.enqueue("state_change", "s1") is True
    assert sender.enqueue("state_change", "s2") is True

    with patch("app.core.ws_backpressure.record_ws_drop") as rec:
        assert sender.enqueue("state_change", "s3") is False
        rec.assert_called_once_with("t2", "event")

    payloads = [e.payload for e in sender._queue]
    assert payloads == ["s1", "s2"]


@pytest.mark.asyncio
async def test_audio_drop_falls_back_to_full_when_no_audio_in_queue():
    """큐에 drop 대상 audio 가 없으면 'full' 로 기록하고 이번 audio 도 drop."""
    ws = _StubWS()
    sender = BoundedWSSender(ws=ws, tenant_id="t3", max_queue_size=2)

    # 비-audio 로만 큐를 채움
    sender.enqueue("state_change", "s1")
    sender.enqueue("state_change", "s2")

    with patch("app.core.ws_backpressure.record_ws_drop") as rec:
        assert sender.enqueue("tts_ready", "audio_new") is False
        rec.assert_called_once_with("t3", "full")

    # 큐 변동 없음
    assert [e.payload for e in sender._queue] == ["s1", "s2"]


# ── 큐 게이지 ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_queue_depth_gauge_updates_on_enqueue():
    ws = _StubWS()
    sender = BoundedWSSender(ws=ws, tenant_id="tg", max_queue_size=3)

    with patch("app.core.ws_backpressure.set_ws_queue_depth") as g:
        sender.enqueue("state_change", "x")
        sender.enqueue("state_change", "y")
        # 각 enqueue 성공 후 1회씩 호출
        calls = [c.args for c in g.call_args_list]
        assert ("tg", 1) in calls
        assert ("tg", 2) in calls


# ── 라이프사이클 / 에러 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_closed_sender_rejects_enqueue():
    ws = _StubWS()
    sender = BoundedWSSender(ws=ws, tenant_id="tc", max_queue_size=4)
    await sender.start()
    await sender.close()

    assert sender.is_closed is True
    # close 이후 enqueue 는 언제나 False
    assert sender.enqueue("state_change", "zz") is False


@pytest.mark.asyncio
async def test_send_failure_marks_sender_closed():
    """ws.send_text 가 throw 하면 drain loop 가 _closed 를 True 로 전이."""
    ws = _StubWS(fail_after=0)
    sender = BoundedWSSender(ws=ws, tenant_id="tf", max_queue_size=4)
    await sender.start()

    sender.enqueue("state_change", "will_fail")

    # drain 에 시간 주기
    for _ in range(20):
        if sender.is_closed:
            break
        await asyncio.sleep(0.01)

    assert sender.is_closed is True
    await sender.close()


@pytest.mark.asyncio
async def test_close_is_idempotent():
    ws = _StubWS()
    sender = BoundedWSSender(ws=ws, tenant_id="ti", max_queue_size=2)
    await sender.start()
    await sender.close()
    # 두 번째 close 가 에러 없이 반환해야 함
    await sender.close()
    assert sender.is_closed is True


# ── 상수 sanity ──────────────────────────────────────────────────────────────


def test_audio_event_name_constant_includes_tts_ready():
    """drop-oldest 대상에 TTS 이벤트 이름이 포함되는지 회귀 방지용."""
    assert "tts_ready" in _AUDIO_EVENT_NAMES


def test_queued_event_structure():
    evt = QueuedEvent(name="state_change", payload="{}")
    assert evt.name == "state_change"
    assert evt.payload == "{}"
