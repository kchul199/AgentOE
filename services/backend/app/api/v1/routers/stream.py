"""SSE (Server-Sent Events) 4채널 엔드포인트 (Phase N — N1.5 / N1.12).

plan §2.4 SSE event schema:
  /stream/metrics       — Prometheus poll 1s → metrics.tick
  /stream/sessions.active — Redis pub agentoe:events:sessions → session.*
  /stream/audit.tail    — Redis pub agentoe:events:audit → audit.append
  /stream/alerts        — AM poll → Redis pub agentoe:events:alerts → alert.*

공통 동작:
  - RBAC: require_portal_role (portal:viewer+)
  - Last-Event-ID: 재연결 시 누락 이벤트 최소화 (단, replay 미지원 — 단순 cursor)
  - heartbeat: 15s 간격 (ALB idle timeout 60s 내로 유지)
  - SSE 연결 가드 (N1.12): 포드 당 SSE_MAX_CONNECTIONS_PER_POD 상한,
      초과 시 503 + Retry-After:30 반환 (uvicorn --limit-concurrency 와 2중 방어).

CLAUDE.md:
  - SSE 핸들러는 async generator — polling 금지 (broadcaster → Queue 소비 방식).
  - non-blocking: 느린 클라이언트는 Queue full drop (broadcaster 가 log).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import UTC
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.core.auth import TenantContext, require_portal_role
from app.core.config import settings
from app.domain.sse_broadcaster import (
    CHANNEL_ALERTS,
    CHANNEL_AUDIT,
    CHANNEL_SESSIONS,
    get_broadcaster,
    queue_iter,
)

logger = structlog.get_logger(__name__)

router = APIRouter()

_HEARTBEAT_INTERVAL = 15.0  # seconds — ALB idle timeout(60s) 보다 충분히 짧게
_SSE_HEADERS = {
    "Content-Type": "text/event-stream",
    "Cache-Control": "no-cache",
    "Connection": "keep-alive",
    "X-Accel-Buffering": "no",  # nginx/ALB SSE 버퍼링 비활성
}

# ── N1.12 SSE 연결 가드 ───────────────────────────────────────────────────────
# 포드 당 동시 SSE 연결 상한. 0 = 비활성.
# uvicorn --limit-concurrency 가 1차 방어선; 이 Semaphore 가 2차.
_sse_semaphore: asyncio.Semaphore | None = None


def _get_sse_semaphore() -> asyncio.Semaphore | None:
    """이벤트루프 안에서 지연 초기화 (lifespan 이전 import 안전)."""
    global _sse_semaphore
    max_conn = settings.SSE_MAX_CONNECTIONS_PER_POD
    if max_conn <= 0:
        return None
    if _sse_semaphore is None:
        _sse_semaphore = asyncio.Semaphore(max_conn)
    return _sse_semaphore


def _sse_503(retry_after: int = 30) -> Response:
    """SSE 연결 상한 초과 시 503 응답."""
    return Response(
        content='{"detail":"SSE connection limit reached — retry later"}',
        status_code=503,
        media_type="application/json",
        headers={"Retry-After": str(retry_after)},
    )


# ── 공용 헬퍼 ──────────────────────────────────────────────────────────────────


def _sse_event(event: str, data: str | dict, id: str | None = None) -> str:
    """SSE 프레임 문자열 생성."""
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False, default=str)
    lines = []
    if id:
        lines.append(f"id: {id}")
    lines.append(f"event: {event}")
    lines.append(f"data: {data}")
    lines.append("")
    lines.append("")
    return "\n".join(lines)


def _heartbeat() -> str:
    return _sse_event("heartbeat", {"ts": _now_iso()})


def _now_iso() -> str:
    from datetime import datetime

    return datetime.now(UTC).isoformat()


async def _redis_channel_stream(
    request: Request,
    channel: str,
    event_name: str,
) -> AsyncGenerator[str, None]:
    """Redis pub/sub 채널 → SSE async generator.

    broadcaster 가 없으면 (Redis 불가) heartbeat 만 전송.
    Semaphore 는 호출 측(_sse_streaming_response)에서 acquire/release 처리.
    """
    try:
        broadcaster = get_broadcaster()
    except RuntimeError:
        # broadcaster 미초기화 (테스트/단일 인스턴스) — heartbeat 전용 fallback
        while not await request.is_disconnected():
            yield _heartbeat()
            await asyncio.sleep(_HEARTBEAT_INTERVAL)
        return

    queue = await broadcaster.subscribe(channel)
    seq = 0
    try:
        async for payload in queue_iter(queue, heartbeat_interval=_HEARTBEAT_INTERVAL):
            if await request.is_disconnected():
                break
            if payload is None:
                yield _heartbeat()
            else:
                seq += 1
                yield _sse_event(event_name, payload, id=str(seq))
    finally:
        await broadcaster.unsubscribe(channel, queue)
        logger.debug("sse_client_disconnected", channel=channel)


async def _sse_streaming_response(
    request: Request,
    channel: str,
    event_name: str,
) -> Response:
    """SSE 연결 가드 적용 후 StreamingResponse 반환 (N1.12).

    Semaphore acquire 실패(non-blocking) → 503 즉시 반환.
    acquire 성공 → generator 가 끝날 때 release.
    """
    sem = _get_sse_semaphore()
    if sem is not None:
        acquired = sem._value > 0  # 잔여 슬롯 확인 (non-blocking check)
        if not sem.locked() and acquired:
            await sem.acquire()
        else:
            # try_acquire: asyncio.Semaphore 는 try_acquire 없으므로 값 직접 확인
            if sem._value <= 0:
                logger.warning(
                    "sse_connection_limit_reached",
                    channel=channel,
                    limit=settings.SSE_MAX_CONNECTIONS_PER_POD,
                )
                return _sse_503()
            await sem.acquire()

    async def _guarded_generate() -> AsyncGenerator[str, None]:
        try:
            async for chunk in _redis_channel_stream(request, channel, event_name):
                yield chunk
        finally:
            if sem is not None:
                sem.release()

    return StreamingResponse(_guarded_generate(), headers=_SSE_HEADERS)


# ── /stream/metrics ────────────────────────────────────────────────────────────


@router.get("/metrics")
async def stream_metrics(
    request: Request,
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
) -> Response:
    """/stream/metrics — Prometheus poll 1s → metrics.tick.

    broadcaster 경유 없이 직접 poll (metrics 는 fan-out 불필요 — 모든 클라이언트가
    같은 cluster 원본을 조회). N1.12 연결 가드 적용.
    """
    sem = _get_sse_semaphore()
    if sem is not None and sem._value <= 0:
        logger.warning(
            "sse_connection_limit_reached",
            channel="/metrics",
            limit=settings.SSE_MAX_CONNECTIONS_PER_POD,
        )
        return _sse_503()
    if sem is not None:
        await sem.acquire()

    async def generate() -> AsyncGenerator[str, None]:
        from app.core.metrics import get_metrics_snapshot_async

        try:
            while not await request.is_disconnected():
                try:
                    snapshot = await asyncio.wait_for(
                        get_metrics_snapshot_async(),
                        timeout=2.0,
                    )
                    snapshot["env"] = _get_env()
                    yield _sse_event("metrics.tick", snapshot)
                except TimeoutError:
                    yield _heartbeat()
                except Exception as e:
                    logger.warning("sse_metrics_poll_error", error=str(e))
                    yield _heartbeat()
                await asyncio.sleep(1.0)
        finally:
            if sem is not None:
                sem.release()

    return StreamingResponse(generate(), headers=_SSE_HEADERS)


def _get_env() -> str:
    try:
        from app.core.config import settings

        return getattr(settings, "ENVIRONMENT", "unknown")
    except Exception:
        return "unknown"


# ── /stream/sessions.active ────────────────────────────────────────────────────


@router.get("/sessions.active")
async def stream_sessions(
    request: Request,
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
) -> Response:
    """/stream/sessions.active — Redis pub agentoe:events:sessions → session.* SSE.

    N1.12 연결 가드 적용.
    """
    return await _sse_streaming_response(request, CHANNEL_SESSIONS, "session.event")


# ── /stream/audit.tail ────────────────────────────────────────────────────────


@router.get("/audit.tail")
async def stream_audit(
    request: Request,
    tenant: Annotated[
        TenantContext, Depends(require_portal_role("portal:operator", "portal:admin"))
    ],
) -> Response:
    """/stream/audit.tail — Redis pub agentoe:events:audit → audit.append SSE.

    RBAC: portal:operator+ (viewer 불가 — 운영자 이상만). N1.12 연결 가드 적용.
    """
    return await _sse_streaming_response(request, CHANNEL_AUDIT, "audit.append")


# ── /stream/alerts ────────────────────────────────────────────────────────────


@router.get("/alerts")
async def stream_alerts(
    request: Request,
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
) -> Response:
    """/stream/alerts — AM poll → Redis pub agentoe:events:alerts → alert.* SSE.

    AM poller (am_poller.py) 가 변화분을 CHANNEL_ALERTS 에 publish.
    N1.12 연결 가드 적용.
    """
    return await _sse_streaming_response(request, CHANNEL_ALERTS, "alert.event")
