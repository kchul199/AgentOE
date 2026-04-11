"""
VBGW (Voice Bridge Gateway) WebSocket Handler
실시간 음성 콜봇 파이프라인 엔드포인트

프로토콜:
  Client → Server: binary (PCM 오디오 청크) 또는 JSON 제어 메시지
  Server → Client: JSON 이벤트

연결 흐름:
  1. ws://host/api/v1/ws/vbgw?token=<JWT>&session_id=<uuid>
  2. 서버: Lease Lock 획득 → Kill Switch 체크 → Redis 재연결 복구 시도
  3. 신규: {"event": "connected",   "session_id": "...", "reconnected": false}
     재연결: {"event": "reconnected", "session_id": "...", "turns_restored": N}
  4. 클라이언트가 audio binary 전송 → STT → PolicyGate → LLM → TTS 파이프라인
  5. 이관: {"action": "request_transfer", "reason": "CUSTOMER_REQUEST"}
  6. 통화 종료: {"action": "hangup"} 또는 WebSocket close

클라이언트 → 서버 (JSON):
  {"action": "start_listening"}
  {"action": "stop_listening"}
  {"action": "hangup"}
  {"action": "ping"}
  {"action": "request_transfer", "reason": "CUSTOMER_REQUEST", "context": "..."}

서버 → 클라이언트 (JSON):
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

WebSocket Close Codes:
  4001 — JWT 인증 실패
  4002 — 세션 Lease Lock 충돌 (중복 연결)
  4003 — Kill Switch 발동 (테넌트 서비스 정지)
  4004 — 이미 종료된 세션 (ENDED)
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.domain.circuit_breaker import CircuitBreakerOpenError
from app.domain.kill_switch import KillSwitchScope, KillSwitchService
from app.domain.policy_gate import PolicyLevel
from app.domain.session_fsm import SessionEventType, SessionFSM, SessionState
from app.repositories.session_repository import SessionRepository
from app.services.ai_pipeline import AIPipeline
from app.services.transfer_service import (
    TransferFallback,
    TransferReason,
    TransferRequest,
    TransferService,
    TransferStatus,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# 오디오 버퍼: 250ms 분량 누적 후 STT 전송
AUDIO_SAMPLE_RATE = 8000
AUDIO_BYTES_PER_MS = AUDIO_SAMPLE_RATE * 2 // 1000   # 16-bit mono
MIN_AUDIO_BYTES = AUDIO_BYTES_PER_MS * 250

# WebSocket keepalive / timeout
PING_INTERVAL_SECONDS = 20
IDLE_TIMEOUT_SECONDS = 120

# 파이프라인 연속 실패 카운터 임계치 → 자동 이관
PIPELINE_FAILURE_THRESHOLD = 3


# ── 이벤트 헬퍼 ────────────────────────────────────────────────────────────────

def _evt(event: str, **kwargs: Any) -> str:
    return json.dumps({"event": event, **kwargs})


async def _send(ws: WebSocket, event: str, **kwargs: Any) -> None:
    try:
        await ws.send_text(_evt(event, **kwargs))
    except Exception as exc:
        logger.debug("WS send failed: %s", exc)


# ── JWT 검증 ──────────────────────────────────────────────────────────────────

def _decode_token(token: str) -> dict | None:
    """WebSocket query param JWT 검증. 실패 시 None 반환."""
    try:
        from jose import jwt
        from app.core.config import settings
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except Exception:  # noqa: BLE001
        return None


# ── VBGWSession ────────────────────────────────────────────────────────────────

class VBGWSession:
    """
    단일 WebSocket 연결의 세션 상태 캡슐화.

    Sprint 3 추가:
    - 재연결 복구: Redis hot-state → FSM.from_snapshot()
    - 상담사 이관: TransferService 통합
    - 연속 실패 카운터: 자동 이관 트리거
    """

    def __init__(
        self,
        ws: WebSocket,
        session_id: str,
        tenant_id: str,
        client_id: str,
        fsm: SessionFSM | None = None,
        history: list[dict] | None = None,
        is_reconnect: bool = False,
    ) -> None:
        self.ws = ws
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.client_id = client_id
        self.fsm = fsm or SessionFSM(SessionState.IDLE)
        self.history: list[dict] = history or []
        self.pipeline = AIPipeline()
        self._transfer_svc = TransferService()
        self._repo = SessionRepository()
        self._audio_buffer = bytearray()
        self._listening = False
        self._last_activity = time.monotonic()
        self._closed = False
        self._is_reconnect = is_reconnect
        self._pipeline_failure_count = 0

    # ── 상태 전이 헬퍼 ──────────────────────────────────────────────────────────

    async def _transition(
        self,
        state: SessionState,
        metadata: dict | None = None,
    ) -> None:
        if self.fsm.can_transition(state):
            self.fsm.transition(state, metadata=metadata)
            await _send(self.ws, "state_change", state=self.fsm.state.value)
        else:
            logger.debug(
                "Skipping invalid transition %s → %s",
                self.fsm.state.value, state.value,
            )

    # ── 오디오 수신 ─────────────────────────────────────────────────────────────

    async def handle_audio(self, data: bytes) -> None:
        """PCM 오디오 청크 수신 — 버퍼 누적 후 파이프라인 실행."""
        self._last_activity = time.monotonic()
        if not self._listening:
            return
        self._audio_buffer.extend(data)
        if len(self._audio_buffer) >= MIN_AUDIO_BYTES:
            audio_snapshot = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            await self._run_pipeline(audio_snapshot)

    # ── 제어 메시지 처리 ────────────────────────────────────────────────────────

    async def handle_control(self, msg: dict) -> None:
        """JSON 제어 메시지 처리."""
        action = msg.get("action", "")
        self._last_activity = time.monotonic()

        if action == "start_listening":
            self._listening = True
            self._audio_buffer.clear()
            await self._transition(SessionState.LISTENING)

        elif action == "stop_listening":
            self._listening = False
            if len(self._audio_buffer) > 0:
                audio_snapshot = bytes(self._audio_buffer)
                self._audio_buffer.clear()
                await self._run_pipeline(audio_snapshot)

        elif action == "hangup":
            await self._end_session(reason="client_hangup")

        elif action == "ping":
            await _send(self.ws, "pong")

        elif action == "request_transfer":
            await self._handle_transfer_request(msg)

        else:
            logger.warning("Unknown action: %s", action)
            await _send(self.ws, "error", code="UNKNOWN_ACTION",
                        message=f"Unknown action: {action}")

    # ── AI 파이프라인 실행 ──────────────────────────────────────────────────────

    async def _run_pipeline(self, audio_bytes: bytes) -> None:
        """STT → PolicyGate → LLM → TTS 파이프라인 비동기 실행."""
        await self._transition(SessionState.SPEAKING_DETECTED)

        # 이관 진행 중이면 파이프라인 실행 차단
        if self.fsm.is_transfer_in_progress:
            await _send(self.ws, "error", code="TRANSFER_IN_PROGRESS",
                        message="상담사 이관 중입니다. 잠시만 기다려 주세요.")
            return

        try:
            result = await self.pipeline.process(
                audio_bytes=audio_bytes,
                session_id=self.session_id,
                tenant_id=self.tenant_id,
                fsm=self.fsm,
                history=self.history,
                policy_level=PolicyLevel.G1,
            )
            self._pipeline_failure_count = 0  # 성공 시 리셋

        except CircuitBreakerOpenError as exc:
            self._pipeline_failure_count += 1
            logger.error("Circuit breaker open: %s (failures=%d)",
                         exc, self._pipeline_failure_count)
            await _send(self.ws, "error", code="SERVICE_UNAVAILABLE",
                        message=f"AI 서비스 일시 장애: {exc.service_name}")
            await self._check_auto_transfer("REPEATED_FAILURE")
            await self._transition(SessionState.LISTENING)
            return

        except Exception as exc:  # noqa: BLE001
            self._pipeline_failure_count += 1
            logger.exception("Pipeline error (failures=%d)", self._pipeline_failure_count)
            await _send(self.ws, "error", code="PIPELINE_ERROR", message=str(exc))
            await self._check_auto_transfer("REPEATED_FAILURE")
            await self._transition(SessionState.LISTENING)
            return

        # STT 결과 전송
        await _send(self.ws, "stt_result", text=result.stt_text, is_final=True)

        # 상담사 이관 의도 감지
        if TransferService.detect_transfer_intent(result.stt_text):
            logger.info("Transfer intent detected in STT text")
            await self._trigger_transfer(
                reason=TransferReason.CUSTOMER_REQUEST,
                context_hint=result.stt_text,
            )
            return

        if not result.policy_allowed:
            # G4/G5 정책 차단 → 이관 트리거
            policy_lvl = result.policy_level
            if policy_lvl in ("G4", "G5"):
                await self._trigger_transfer(
                    reason=TransferReason.G4_POLICY if policy_lvl == "G4"
                           else TransferReason.G5_POLICY,
                    context_hint=result.stt_text,
                )
            else:
                await _send(self.ws, "error", code="POLICY_BLOCKED",
                            message=f"정책 차단 (level={policy_lvl})")
                await self._transition(SessionState.LISTENING)
            return

        # LLM + TTS 결과 전송
        await _send(self.ws, "llm_chunk", text=result.llm_text,
                    is_final=True, is_filler=result.filler_triggered)

        if result.tts_audio:
            await _send(self.ws, "tts_ready",
                        audio_b64=base64.b64encode(result.tts_audio).decode(),
                        text=result.llm_text)

        # 히스토리 업데이트
        self.history.append({"role": "user",      "content": result.stt_text})
        self.history.append({"role": "assistant",  "content": result.llm_text})
        if len(self.history) > 20:
            self.history = self.history[-20:]

        await _send(self.ws, "pipeline_done",
                    latency=result.latency,
                    policy_level=result.policy_level)

        # 세션 상태 영구 저장 (MongoDB + Redis)
        await self._persist_turn(
            user_text=result.stt_text,
            ai_text=result.llm_text,
            latency=result.latency,
            policy_level=result.policy_level,
        )

        await self._transition(SessionState.LISTENING)

    # ── 상담사 이관 처리 ────────────────────────────────────────────────────────

    async def _handle_transfer_request(self, msg: dict) -> None:
        """클라이언트 명시적 이관 요청 처리."""
        try:
            reason = TransferReason(msg.get("reason", "MANUAL"))
        except ValueError:
            reason = TransferReason.MANUAL

        await self._trigger_transfer(
            reason=reason,
            context_hint=msg.get("context", ""),
        )

    async def _trigger_transfer(
        self,
        reason: TransferReason,
        context_hint: str = "",
    ) -> None:
        """이관 요청 생성 및 TransferService 호출."""
        context_summary = TransferService.build_context_summary(
            self.history, context_hint
        )
        transfer_req = TransferRequest(
            session_id=self.session_id,
            tenant_id=self.tenant_id,
            reason=reason,
            context_summary=context_summary,
            priority=3 if reason in (TransferReason.G4_POLICY, TransferReason.G5_POLICY) else 5,
        )

        result = await self._transfer_svc.request(
            fsm=self.fsm,
            transfer_req=transfer_req,
            fallback=TransferFallback.CALLBACK,
        )

        await _send(
            self.ws, "transfer_update",
            status=result.status.value,
            message=result.message,
            agent=result.agent_name or result.agent_id,
        )

        if result.status == TransferStatus.ACCEPTED:
            # 이관 완료 → 세션 종료
            await self._end_session(reason="transfer_accepted")

        elif result.fallback_action == TransferFallback.AI_RESUME:
            # AI 재응대 복귀
            await _send(self.ws, "state_change", state=self.fsm.state.value)

    async def _check_auto_transfer(self, reason_str: str) -> None:
        """연속 파이프라인 실패 임계치 초과 시 자동 이관 트리거."""
        if self._pipeline_failure_count >= PIPELINE_FAILURE_THRESHOLD:
            self.fsm.record_event(
                SessionEventType.VENDOR_DEGRADED,
                metadata={"failure_count": self._pipeline_failure_count},
            )
            await self._trigger_transfer(
                reason=TransferReason.REPEATED_FAILURE,
                context_hint="AI 서비스 연속 장애로 인한 자동 이관",
            )

    # ── 세션 영구 저장 ──────────────────────────────────────────────────────────

    async def _persist_turn(
        self,
        user_text: str,
        ai_text: str,
        latency: dict,
        policy_level: str,
    ) -> None:
        """파이프라인 1턴 완료 후 MongoDB + Redis 저장."""
        try:
            await self._repo.save_turn(
                session_id=self.session_id,
                fsm=self.fsm,
                user_text=user_text,
                ai_text=ai_text,
                latency=latency,
                policy_level=policy_level,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist turn: %s", exc)

    # ── 세션 종료 ───────────────────────────────────────────────────────────────

    async def _end_session(self, reason: str = "normal") -> None:
        if self._closed:
            return
        self._closed = True
        if self.fsm.can_transition(SessionState.ENDED):
            self.fsm.transition(SessionState.ENDED)
        await _send(self.ws, "state_change", state=SessionState.ENDED.value)

        try:
            await self._repo.end_session(
                self.session_id, reason=reason, fsm=self.fsm
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist session end: %s", exc)

        logger.info("VBGW session ended",
                    extra={"session_id": self.session_id, "reason": reason})

    @property
    def is_idle_timeout(self) -> bool:
        return (time.monotonic() - self._last_activity) > IDLE_TIMEOUT_SECONDS


# ── 세션 복구 헬퍼 ────────────────────────────────────────────────────────────

async def _restore_or_create_session(
    ws: WebSocket,
    session_id: str,
    tenant_id: str,
    client_id: str,
    repo: SessionRepository,
) -> VBGWSession | None:
    """
    재연결 복구 시도.

    Returns:
        VBGWSession — 신규 또는 복구된 세션
        None — 세션이 ENDED 상태라 연결 불가
    """
    hot_state = await repo.restore_hot_state(session_id)

    if hot_state is None:
        # 신규 세션 (MongoDB에도 없음)
        fsm = SessionFSM(SessionState.IDLE)
        session = VBGWSession(
            ws=ws,
            session_id=session_id,
            tenant_id=tenant_id,
            client_id=client_id,
            fsm=fsm,
            is_reconnect=False,
        )
        # MongoDB에 세션 레코드 생성
        await repo.create({
            "session_id": session_id,
            "tenant_id": tenant_id,
            "client_id": client_id,
            "status": SessionState.IDLE.value,
        })
        await _send(ws, "connected", session_id=session_id, reconnected=False)
        return session

    # 이미 종료된 세션이면 연결 거부
    if hot_state.get("status") == SessionState.ENDED.value:
        return None

    # 복구: FSM 스냅샷 복원
    fsm_snapshot = hot_state.get("fsm_snapshot", {"state": "IDLE", "events": []})
    try:
        fsm = SessionFSM.from_snapshot(fsm_snapshot)
        # 재연결 직후 상태를 LISTENING으로 복귀 (안전한 상태)
        if fsm.state not in (SessionState.IDLE, SessionState.LISTENING, SessionState.ENDED):
            if fsm.can_transition(SessionState.LISTENING):
                fsm.transition(SessionState.LISTENING)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FSM snapshot restore failed, using IDLE: %s", exc)
        fsm = SessionFSM(SessionState.IDLE)

    history: list[dict] = hot_state.get("history", [])
    turns_restored = len(history) // 2

    session = VBGWSession(
        ws=ws,
        session_id=session_id,
        tenant_id=tenant_id,
        client_id=client_id,
        fsm=fsm,
        history=history,
        is_reconnect=True,
    )

    logger.info(
        "VBGW session reconnected",
        extra={
            "session_id": session_id,
            "turns_restored": turns_restored,
            "state": fsm.state.value,
        },
    )
    await _send(
        ws, "reconnected",
        session_id=session_id,
        turns_restored=turns_restored,
        state=fsm.state.value,
    )
    return session


# ── WebSocket 엔드포인트 ────────────────────────────────────────────────────────

@router.websocket("/ws/vbgw")
async def vbgw_websocket(
    ws: WebSocket,
    token: str = Query(..., description="JWT access token"),
    session_id: str = Query(..., description="Unique session UUID"),
) -> None:
    """
    VBGW 실시간 음성 WebSocket 엔드포인트.

    연결 URL: ws://host/api/v1/ws/vbgw?token=<JWT>&session_id=<UUID>
    """
    # 1. JWT 검증
    payload = _decode_token(token)
    if not payload:
        await ws.close(code=4001, reason="Invalid or expired token")
        return

    tenant_id: str = payload.get("tenant_id", "")
    client_id: str = payload.get("sub", "")

    if not tenant_id or not client_id:
        await ws.close(code=4001, reason="Missing tenant_id or sub in token")
        return

    # 2. Kill Switch 체크
    try:
        ks_service = KillSwitchService()
        if await ks_service.is_active(KillSwitchScope.TENANT, tenant_id):
            await ws.close(code=4003, reason="Tenant service is temporarily suspended")
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("Kill switch check failed, proceeding: %s", exc)

    # 3. WebSocket 연결 수락
    await ws.accept()

    repo = SessionRepository()

    # 4. Lease Lock 획득 (중복 연결 방지)
    lease_acquired = await repo.acquire_session_lease(session_id)
    if not lease_acquired:
        logger.warning("Lease conflict for session %s", session_id)
        await _send(ws, "error", code="LEASE_CONFLICT",
                    message="동일 세션이 이미 연결 중입니다.")
        await ws.close(code=4002, reason="Session lease conflict")
        return

    # 5. 세션 복구 또는 신규 생성
    session = await _restore_or_create_session(
        ws=ws,
        session_id=session_id,
        tenant_id=tenant_id,
        client_id=client_id,
        repo=repo,
    )
    if session is None:
        await _send(ws, "error", code="SESSION_ENDED",
                    message="이미 종료된 세션입니다.")
        await ws.close(code=4004, reason="Session already ended")
        await repo.release_session_lease(session_id)
        return

    logger.info(
        "VBGW WebSocket ready",
        extra={
            "session_id": session_id,
            "tenant_id": tenant_id,
            "reconnect": session._is_reconnect,
        },
    )

    # 6. 메인 수신 루프
    try:
        while True:
            if session.is_idle_timeout:
                logger.info("VBGW idle timeout: %s", session_id)
                await _send(ws, "error", code="IDLE_TIMEOUT",
                            message="비활성으로 인해 세션이 종료됩니다.")
                break

            try:
                data = await asyncio.wait_for(
                    ws.receive(),
                    timeout=PING_INTERVAL_SECONDS,
                )
            except asyncio.TimeoutError:
                await _send(ws, "ping")
                continue

            msg_type = data.get("type")

            if msg_type == "websocket.receive":
                raw_bytes = data.get("bytes")
                raw_text = data.get("text")

                if raw_bytes:
                    await session.handle_audio(raw_bytes)
                elif raw_text:
                    try:
                        await session.handle_control(json.loads(raw_text))
                    except json.JSONDecodeError:
                        await _send(ws, "error", code="INVALID_JSON",
                                    message="잘못된 JSON 형식입니다.")

            elif msg_type == "websocket.disconnect":
                logger.info("VBGW client disconnected: %s", session_id)
                break

    except WebSocketDisconnect:
        logger.info("VBGW WebSocket disconnected: %s", session_id)

    except Exception as exc:  # noqa: BLE001
        logger.exception("Unexpected VBGW error: %s", session_id)
        await _send(ws, "error", code="INTERNAL_ERROR", message=str(exc))

    finally:
        await session._end_session(reason="connection_closed")
        await repo.release_session_lease(session_id)
        try:
            await ws.close()
        except Exception:  # noqa: BLE001
            pass
