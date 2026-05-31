"""
VBGW (Voice Bridge Gateway) WebSocket 어댑터

이 파일의 단일 책임:
  WebSocket 프로토콜 처리 — 연결 수락, 메시지 파싱, 이벤트 직렬화, 연결 해제.

비즈니스 로직은 CallSessionOrchestrator(app/services/call_session_orchestrator.py)
에서 전담한다. 이 파일은 "어떻게 통신하는가"만 알고,
"무엇을 결정하는가"는 모른다.

연결 흐름:
  1. ws://host/api/v1/ws/vbgw?token=<JWT>&session_id=<uuid>
  2. JWT 검증 → Kill Switch → WS accept → Lease Lock → 세션 복구/생성
  3. 초기 이벤트(connected / reconnected) 전송
  4. 수신 루프: binary → handle_audio / text → handle_control
  5. orchestrator.should_close 또는 disconnect → 루프 종료
  6. finally: end_session → release_lease → decrement_admission → ws.close

WebSocket Close Codes:
  4001 — JWT 인증 실패
  4002 — 세션 Lease Lock 충돌 (중복 연결)
  4003 — Kill Switch 발동 (테넌트 서비스 정지)
  4004 — 이미 종료된 세션 (ENDED)
  4005 — Origin 헤더 거부 (화이트리스트 불일치)

클라이언트 → 서버 (JSON 제어):
  {"action": "start_listening"}
  {"action": "stop_listening"}
  {"action": "hangup"}
  {"action": "ping"}
  {"action": "request_transfer", "reason": "CUSTOMER_REQUEST", "context": "..."}

서버 → 클라이언트 (JSON 이벤트):
  {"event": "connected",      "session_id": str, "reconnected": bool}
  {"event": "reconnected",    "session_id": str, "turns_restored": int}
  {"event": "state_change",   "state": str}
  {"event": "stt_result",     "text": str, "is_final": bool}
  {"event": "llm_chunk",      "text": str, "is_final": bool, "is_filler": bool}
  {"event": "tts_ready",      "audio_b64": str, "text": str}
  {"event": "pipeline_done",  "latency": dict, "policy_level": str}
  {"event": "transfer_update","status": str, "message": str, "agent": str|null}
  {"event": "error",          "code": str, "message": str}
  {"event": "pong"}
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.core.config import settings
from app.core.logging import (
    bind_session_context,
    unbind_request_context,
)
from app.core.ws_backpressure import BoundedWSSender
from app.domain.kill_switch import KillSwitchScope, KillSwitchService
from app.middleware.admission_middleware import (
    decrement_session_count,
    increment_session_count,
)
from app.repositories.session_repository import SessionRepository
from app.services.call_session_orchestrator import (
    OutboundEvent,
    restore_or_create_orchestrator,
)

logger = logging.getLogger(__name__)
router = APIRouter()

# keepalive ping 주기 (수신 대기 타임아웃)
PING_INTERVAL_SECONDS = 20

# 파이프라인 실행 최소 오디오 크기 (160 bytes = 20ms @ 8kHz/16bit PCM)
# 이보다 작은 청크는 VAD noise 로 간주해 파이프라인 호출 skip
MIN_AUDIO_BYTES = 160


# ── JWT 검증 ──────────────────────────────────────────────────────────────────


def _decode_token(token: str) -> dict | None:
    """WebSocket query-param JWT 검증. 실패 시 None 반환."""
    try:
        from jose import jwt

        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except Exception:
        return None


# ── Origin 검증 ───────────────────────────────────────────────────────────────
#
# 브라우저 WebSocket 클라이언트는 반드시 Origin 헤더를 보낸다. 값이 화이트리스트에
# 없으면 CSRF 성격의 하이재킹 공격을 의심해 연결을 거부. 비-브라우저(모바일 네이티브,
# 서버사이드 클라이언트) 는 Origin 헤더가 없거나 임의 문자열 — WS_ALLOW_EMPTY_ORIGIN
# 설정으로 제어.


def _is_origin_allowed(origin: str | None) -> bool:
    """settings.WS_ALLOWED_ORIGINS 와 WS_ALLOW_EMPTY_ORIGIN 기반 판정."""
    if origin is None or origin == "":
        return settings.WS_ALLOW_EMPTY_ORIGIN

    allowed = set(settings.WS_ALLOWED_ORIGINS or [])
    # "*" 와일드카드는 의도적으로 지원하지 않음 (CSRF 방어 목적)
    return origin in allowed


# ── 전송 헬퍼 ─────────────────────────────────────────────────────────────────


async def _send_events_direct(ws: WebSocket, events: list[OutboundEvent]) -> None:
    """
    BoundedWSSender 가 아직 준비되지 않은 지점(accept 전/후 에러 경로)에서만 사용.

    - 세션당 총 1~2 이벤트만 보내므로 back-pressure 걱정 없음.
    - sender.close() 이후에도 fallback 으로 쓰인다 (drain 이미 멈춤).
    """
    for evt in events:
        try:
            await ws.send_text(evt.to_json())
        except Exception as exc:
            logger.debug("WS send failed (event=%s): %s", evt.name, exc)


def _enqueue_events(sender: BoundedWSSender, events: list[OutboundEvent]) -> None:
    """
    정상 세션 수명 동안의 이벤트 전송 — BoundedWSSender 를 통한 bounded enqueue.

    drop 은 sender 내부에서 판단/카운트된다. 호출자는 신경쓰지 않아도 됨.
    non-blocking — 이벤트 폭주 시에도 이 함수는 즉시 반환하여 receive loop 지연 없음.
    """
    for evt in events:
        sender.enqueue(evt.name, evt.to_json())


# ── WebSocket 엔드포인트 ──────────────────────────────────────────────────────


@router.websocket("/ws/vbgw")
async def vbgw_websocket(
    ws: WebSocket,
    token: str = Query(..., description="JWT access token"),
    session_id: str = Query(..., description="Unique session UUID"),
) -> None:
    """VBGW 실시간 음성 WebSocket 엔드포인트."""

    # ── 0. Origin 헤더 검증 (accept 전) ──────────────────────────────────
    # 브라우저 WS 클라이언트의 CSRF 성격 하이재킹 방지.
    origin = ws.headers.get("origin")
    if not _is_origin_allowed(origin):
        logger.warning(
            "WS origin rejected: origin=%s allowed=%s",
            origin,
            settings.WS_ALLOWED_ORIGINS,
        )
        await ws.close(code=4005, reason="Origin not allowed")
        return

    # ── 1. JWT 검증 (accept 전) ───────────────────────────────────────────
    payload = _decode_token(token)
    if not payload:
        await ws.close(code=4001, reason="Invalid or expired token")
        return

    tenant_id: str = payload.get("tenant_id", "")
    client_id: str = payload.get("sub", "")

    if not tenant_id or not client_id:
        await ws.close(code=4001, reason="Missing tenant_id or sub in token")
        return

    # ── 2. Kill Switch 체크 (accept 전) ──────────────────────────────────
    try:
        ks = KillSwitchService()
        if await ks.is_active(KillSwitchScope.TENANT, tenant_id):
            await ws.close(code=4003, reason="Tenant service is temporarily suspended")
            return
    except Exception as exc:
        logger.warning("Kill switch check failed, proceeding: %s", exc)

    # ── 3. WebSocket 수락 ─────────────────────────────────────────────────
    await ws.accept()

    # 세션 context 바인딩 — 이후 이 태스크의 모든 structlog 이벤트에
    # session_id / tenant_id / client_id 자동 포함. finally 에서 반드시 clear.
    bind_session_context(
        session_id=session_id,
        tenant_id=tenant_id,
        client_id=client_id,
    )

    # 단일 repo 인스턴스 — 이 핸들러 전체에서 공유
    repo = SessionRepository()

    # ── 4. Lease Lock 획득 (중복 연결 방지) ──────────────────────────────
    if not await repo.acquire_session_lease(session_id):
        logger.warning("Lease conflict: session=%s", session_id)
        await _send_events_direct(
            ws,
            [
                OutboundEvent(
                    "error",
                    {
                        "code": "LEASE_CONFLICT",
                        "message": "동일 세션이 이미 연결 중입니다.",
                    },
                )
            ],
        )
        await ws.close(code=4002, reason="Session lease conflict")
        unbind_request_context()
        return

    # ── 5. 세션 복구 또는 신규 생성 ──────────────────────────────────────
    orchestrator, initial_events = await restore_or_create_orchestrator(
        session_id=session_id,
        tenant_id=tenant_id,
        client_id=client_id,
        repo=repo,
    )

    if orchestrator is None:
        await _send_events_direct(
            ws,
            [
                OutboundEvent(
                    "error",
                    {
                        "code": "SESSION_ENDED",
                        "message": "이미 종료된 세션입니다.",
                    },
                )
            ],
        )
        await ws.close(code=4004, reason="Session already ended")
        await repo.release_session_lease(session_id)
        unbind_request_context()
        return

    # ── 6. BoundedWSSender 시작 + 초기 이벤트 송신 + Admission 증가 ──────
    # back-pressure: 느린 클라이언트가 다른 세션의 메모리를 잠식하지 못하도록
    # 세션당 송신 큐를 분리하고 overflow 시 drop 정책 적용.
    sender = BoundedWSSender(ws=ws, tenant_id=tenant_id)
    await sender.start()
    _enqueue_events(sender, initial_events)
    await increment_session_count(tenant_id)

    logger.info(
        "VBGW ready: session=%s tenant=%s reconnect=%s",
        session_id,
        tenant_id,
        any(e.name == "reconnected" for e in initial_events),
    )

    # ── 7. 메인 수신 루프 ─────────────────────────────────────────────────
    try:
        while True:
            # 비활성 타임아웃 체크
            if orchestrator.is_idle_timeout:
                logger.info("Idle timeout: session=%s", session_id)
                _enqueue_events(
                    sender,
                    [
                        OutboundEvent(
                            "error",
                            {
                                "code": "IDLE_TIMEOUT",
                                "message": "비활성으로 인해 세션이 종료됩니다.",
                            },
                        )
                    ],
                )
                break

            # ping 주기로 수신 대기
            try:
                data = await asyncio.wait_for(
                    ws.receive(),
                    timeout=PING_INTERVAL_SECONDS,
                )
            except TimeoutError:
                _enqueue_events(sender, [OutboundEvent("ping")])
                continue

            msg_type = data.get("type")

            if msg_type == "websocket.receive":
                raw_bytes = data.get("bytes")
                raw_text = data.get("text")

                if raw_bytes:
                    events = await orchestrator.handle_audio(raw_bytes)
                elif raw_text:
                    try:
                        events = await orchestrator.handle_control(json.loads(raw_text))
                    except json.JSONDecodeError:
                        events = [
                            OutboundEvent(
                                "error",
                                {
                                    "code": "INVALID_JSON",
                                    "message": "잘못된 JSON 형식입니다.",
                                },
                            )
                        ]
                else:
                    events = []

                _enqueue_events(sender, events)

                # 오케스트레이터가 세션 종료를 결정했으면 루프 탈출
                if orchestrator.should_close:
                    break

            elif msg_type == "websocket.disconnect":
                logger.info("Client disconnected: session=%s", session_id)
                break

    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: session=%s", session_id)

    except Exception as exc:
        logger.exception("Unexpected error: session=%s", session_id)
        _enqueue_events(
            sender,
            [
                OutboundEvent(
                    "error",
                    {
                        "code": "INTERNAL_ERROR",
                        "message": str(exc),
                    },
                )
            ],
        )

    finally:
        # 단일 종료 경로 — orchestrator.end_session()은 _closed 플래그로
        # 중복 실행을 자체 차단하므로 항상 안전하게 호출 가능
        end_events = await orchestrator.end_session(reason="connection_closed")
        # 마지막 end 이벤트는 "반드시 나가야" 하는 것이므로 sender 를 먼저
        # 닫고 direct send 로 기록을 시도한다. sender drain loop 가 살아있는
        # 동안 남은 큐를 flush 할 시간을 주지 않으면 ws.close() 가 먼저 나가
        # 유실될 수 있기 때문.
        await sender.close()
        await _send_events_direct(ws, end_events)

        await repo.release_session_lease(session_id)
        await decrement_session_count(tenant_id)

        with contextlib.suppress(Exception):
            await ws.close()

        # 구조화 로그 context 정리 (Track 2-e) — 다음 WS 연결/태스크 재사용
        # 시 이전 session_id 가 섞여 들어가는 것을 방지. clear 는 '무슨 일이
        # 있어도 전부 비운다' 가 목적이므로 try/except 로 감싸지 않는다.
        unbind_request_context()
