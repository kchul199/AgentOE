"""
CallSessionOrchestrator — 콜 세션 비즈니스 로직 전담 서비스.

설계 원칙:
  - WebSocket / 전송 계층과 완전히 분리. `WebSocket` 객체를 일절 참조하지 않는다.
  - 모든 public 메서드는 클라이언트로 보낼 OutboundEvent 리스트를 반환한다.
    호출자(vbgw.py)가 이를 직렬화해서 전송할 책임을 진다.
  - SessionRepository 인스턴스는 외부에서 주입받는다 (단일 인스턴스, DI 친화적).
  - policy_level은 테넌트 설정에서 읽어야 하며, 현재는 기본값 G1을 사용한다.
    TODO: TenantRepository.get_policy_level(tenant_id) 로 교체 예정.

OutboundEvent 흐름:
  orchestrator.handle_audio(pcm) → [stt_result, llm_chunk, tts_ready, pipeline_done, state_change]
  orchestrator.handle_control(msg) → [state_change | pong | error | transfer_update | ...]
  orchestrator.end_session(reason) → [state_change(ENDED)]

세션 종료 경로는 end_session() 하나로 통일. should_close 플래그로
호출자에게 루프 종료를 알린다.
"""
from __future__ import annotations

import base64
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import structlog

from app.core.logging import bind_pipeline_context, unbind_keys
from app.core.metrics import record_pipeline_call
from app.domain.circuit_breaker import CircuitBreakerOpenError
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

logger = structlog.get_logger(__name__)

# ── 상수 ──────────────────────────────────────────────────────────────────────

# 오디오 버퍼: 250 ms 분량 누적 후 STT 전송
AUDIO_SAMPLE_RATE = 8000
AUDIO_BYTES_PER_MS = AUDIO_SAMPLE_RATE * 2 // 1000  # 16-bit mono
MIN_AUDIO_BYTES = AUDIO_BYTES_PER_MS * 250

# WebSocket 비활성 타임아웃
IDLE_TIMEOUT_SECONDS = 120

# 연속 파이프라인 실패 임계치 → 자동 이관
PIPELINE_FAILURE_THRESHOLD = 3

# in-memory 히스토리 최대 보관 턴 수 (Redis hot-state 동기화 기준)
MAX_HISTORY_IN_MEMORY = 20

# 이관 우선순위 — 낮을수록 긴급
_HIGH_PRIORITY_REASONS = {TransferReason.G4_POLICY, TransferReason.G5_POLICY}


def _transfer_priority(reason: TransferReason) -> int:
    """정책 위반 이관은 우선순위 3(긴급), 그 외는 5(일반)."""
    return 3 if reason in _HIGH_PRIORITY_REASONS else 5


# ── OutboundEvent ─────────────────────────────────────────────────────────────

@dataclass
class OutboundEvent:
    """
    오케스트레이터가 호출자(vbgw.py)에게 반환하는 클라이언트 전송 이벤트.

    호출자는 to_json()으로 직렬화해서 WebSocket으로 전송한다.
    """
    name: str
    payload: dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({"event": self.name, **self.payload})


# ── CallSessionOrchestrator ───────────────────────────────────────────────────

class CallSessionOrchestrator:
    """
    단일 WebSocket 세션의 모든 비즈니스 로직을 담당하는 오케스트레이터.

    외부 의존성:
      - SessionRepository : 주입받음 (단일 인스턴스)
      - AIPipeline        : 내부 생성 (stateless)
      - TransferService   : 내부 생성 (stateless)

    WebSocket 참조 없음 → 전송 계층과 완전히 분리됨.
    """

    def __init__(
        self,
        session_id: str,
        tenant_id: str,
        client_id: str,
        repo: SessionRepository,
        fsm: SessionFSM | None = None,
        history: list[dict] | None = None,
        policy_level: PolicyLevel = PolicyLevel.G1,
    ) -> None:
        self.session_id = session_id
        self.tenant_id = tenant_id
        self.client_id = client_id
        self._repo = repo
        self.fsm: SessionFSM = fsm or SessionFSM(SessionState.IDLE)
        self.history: list[dict] = list(history or [])
        self._policy_level = policy_level

        # 내부 서비스 (stateless → 인스턴스 공유 불필요)
        self._pipeline = AIPipeline()
        self._transfer_svc = TransferService()

        # 오디오 버퍼 상태
        self._audio_buffer = bytearray()
        self._listening = False

        # 세션 상태 플래그
        self._last_activity = time.monotonic()
        self._closed = False
        self._should_close = False
        self._pipeline_failure_count = 0

    # ── 공개 프로퍼티 ─────────────────────────────────────────────────────────

    @property
    def should_close(self) -> bool:
        """True 이면 WS 핸들러는 수신 루프를 종료해야 한다."""
        return self._should_close

    @property
    def is_idle_timeout(self) -> bool:
        return (time.monotonic() - self._last_activity) > IDLE_TIMEOUT_SECONDS

    # ── 이벤트 생성 헬퍼 ─────────────────────────────────────────────────────

    def _evt(self, name: str, **payload: Any) -> OutboundEvent:
        return OutboundEvent(name=name, payload=payload)

    def _state_evt(self) -> OutboundEvent:
        return self._evt("state_change", state=self.fsm.state.value)

    def _do_transition(
        self, state: SessionState, metadata: dict | None = None
    ) -> OutboundEvent | None:
        """FSM 전이 시도. 성공하면 state_change 이벤트, 실패(불가)하면 None."""
        if self.fsm.can_transition(state):
            self.fsm.transition(state, metadata=metadata)
            return self._state_evt()
        return None

    # ── 공개 메서드 ───────────────────────────────────────────────────────────

    async def handle_audio(self, data: bytes) -> list[OutboundEvent]:
        """PCM 오디오 청크 수신 → 버퍼 누적 → 임계치 도달 시 파이프라인 실행."""
        self._last_activity = time.monotonic()
        if not self._listening:
            return []
        self._audio_buffer.extend(data)
        if len(self._audio_buffer) >= MIN_AUDIO_BYTES:
            snapshot = bytes(self._audio_buffer)
            self._audio_buffer.clear()
            return await self._run_pipeline(snapshot)
        return []

    async def handle_control(self, msg: dict) -> list[OutboundEvent]:
        """JSON 제어 메시지 라우팅."""
        action = msg.get("action", "")
        self._last_activity = time.monotonic()

        if action == "start_listening":
            self._listening = True
            self._audio_buffer.clear()
            evts: list[OutboundEvent] = []
            e = self._do_transition(SessionState.LISTENING)
            if e:
                evts.append(e)
            return evts

        if action == "stop_listening":
            self._listening = False
            if self._audio_buffer:
                snapshot = bytes(self._audio_buffer)
                self._audio_buffer.clear()
                return await self._run_pipeline(snapshot)
            return []

        if action == "hangup":
            return await self.end_session(reason="client_hangup")

        if action == "ping":
            return [self._evt("pong")]

        if action == "request_transfer":
            try:
                reason = TransferReason(msg.get("reason", "MANUAL"))
            except ValueError:
                reason = TransferReason.MANUAL
            return await self._trigger_transfer(
                reason=reason,
                context_hint=msg.get("context", ""),
            )

        logger.warning("Unknown control action: %s", action,
                       session_id=self.session_id)
        return [self._evt("error", code="UNKNOWN_ACTION",
                          message=f"Unknown action: {action}")]

    async def end_session(self, reason: str = "normal") -> list[OutboundEvent]:
        """
        세션 종료 — 모든 종료 경로(hangup, transfer, idle, disconnect)가
        이 메서드 하나로 수렴한다. _closed 플래그로 중복 실행을 원천 차단.
        """
        if self._closed:
            return []
        self._closed = True
        self._should_close = True

        e = self._do_transition(SessionState.ENDED)

        try:
            await self._repo.end_session(
                self.session_id, reason=reason, fsm=self.fsm
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to persist session end: %s", exc,
                           session_id=self.session_id)

        logger.info("Session ended", session_id=self.session_id, reason=reason)
        return [e] if e else [self._evt("state_change",
                                        state=SessionState.ENDED.value)]

    # ── 내부 파이프라인 실행 ──────────────────────────────────────────────────

    async def _run_pipeline(self, audio_bytes: bytes) -> list[OutboundEvent]:
        """
        STT → PolicyGate → LLM → TTS 파이프라인 실행.

        반환 이벤트 순서:
          state_change(SPEAKING_DETECTED)
          stt_result
          [transfer_update | error | llm_chunk + tts_ready + pipeline_done]
          state_change(LISTENING)
        """
        events: list[OutboundEvent] = []

        e = self._do_transition(SessionState.SPEAKING_DETECTED)
        if e:
            events.append(e)

        # 이관 진행 중이면 파이프라인 차단
        if self.fsm.is_transfer_in_progress:
            events.append(self._evt("error", code="TRANSFER_IN_PROGRESS",
                                    message="상담사 이관 중입니다. 잠시만 기다려 주세요."))
            return events

        # ── 파이프라인 실행 ────────────────────────────────────────────────
        # bind_pipeline_context 로 바인딩한 stage 는 turn 이 끝나면 반드시
        # unbind 하여 후속 이벤트(ex: WS ping, transfer update) 로그에 stale
        # 값이 묻어나지 않도록 한다. (Track 2-e 로그 컨텍스트 누수 방지)
        try:
            bind_pipeline_context(stage="pipeline")
            result = await self._pipeline.process(
                audio_bytes=audio_bytes,
                session_id=self.session_id,
                tenant_id=self.tenant_id,
                fsm=self.fsm,
                history=self.history,
                policy_level=self._policy_level,
            )
            self._pipeline_failure_count = 0  # 성공 시 리셋

        except CircuitBreakerOpenError as exc:
            self._pipeline_failure_count += 1
            logger.error("Circuit breaker open: %s (failures=%d)",
                         exc, self._pipeline_failure_count,
                         session_id=self.session_id)
            events.append(self._evt("error", code="SERVICE_UNAVAILABLE",
                                    message=f"AI 서비스 일시 장애: {exc.service_name}"))
            events.extend(await self._check_auto_transfer())
            e = self._do_transition(SessionState.LISTENING)
            if e:
                events.append(e)
            return events

        except Exception as exc:  # noqa: BLE001
            self._pipeline_failure_count += 1
            logger.exception("Pipeline unexpected error (failures=%d)",
                             self._pipeline_failure_count,
                             session_id=self.session_id)
            events.append(self._evt("error", code="PIPELINE_ERROR",
                                    message=str(exc)))
            events.extend(await self._check_auto_transfer())
            e = self._do_transition(SessionState.LISTENING)
            if e:
                events.append(e)
            return events

        finally:
            # stage / policy_level 은 파이프라인 내부용 스냅샷이므로
            # turn 이 끝나면 반드시 해제 (다음 turn/ping/transfer 로그 오염 방지)
            unbind_keys("pipeline_stage", "policy_level")

        # ── STT 결과 ──────────────────────────────────────────────────────
        events.append(self._evt("stt_result", text=result.stt_text, is_final=True))

        # ── 이관 의도 감지 ────────────────────────────────────────────────
        if TransferService.detect_transfer_intent(result.stt_text):
            logger.info("Transfer intent detected in STT",
                        session_id=self.session_id)
            events.extend(await self._trigger_transfer(
                reason=TransferReason.CUSTOMER_REQUEST,
                context_hint=result.stt_text,
            ))
            return events

        # ── 정책 차단 분기 ────────────────────────────────────────────────
        if not result.policy_allowed:
            policy_lvl = result.policy_level
            if policy_lvl in ("G4", "G5"):
                reason = (TransferReason.G4_POLICY
                          if policy_lvl == "G4" else TransferReason.G5_POLICY)
                events.extend(await self._trigger_transfer(
                    reason=reason, context_hint=result.stt_text,
                ))
            else:
                events.append(self._evt("error", code="POLICY_BLOCKED",
                                        message=f"정책 차단 (level={policy_lvl})"))
                e = self._do_transition(SessionState.LISTENING)
                if e:
                    events.append(e)
            return events

        # ── 정상 경로: LLM + TTS 결과 ────────────────────────────────────
        events.append(self._evt("llm_chunk", text=result.llm_text,
                                is_final=True, is_filler=result.filler_triggered))

        if result.tts_audio:
            events.append(self._evt(
                "tts_ready",
                audio_b64=base64.b64encode(result.tts_audio).decode(),
                text=result.llm_text,
            ))

        # ── 히스토리 관리 ─────────────────────────────────────────────────
        self.history.append({"role": "user",      "content": result.stt_text})
        self.history.append({"role": "assistant",  "content": result.llm_text})
        if len(self.history) > MAX_HISTORY_IN_MEMORY:
            self.history = self.history[-MAX_HISTORY_IN_MEMORY:]

        events.append(self._evt("pipeline_done",
                                latency=result.latency,
                                policy_level=result.policy_level))

        # ── 영속성 저장 ───────────────────────────────────────────────────
        await self._persist_turn(
            user_text=result.stt_text,
            ai_text=result.llm_text,
            latency=result.latency,
            policy_level=result.policy_level,
        )

        e = self._do_transition(SessionState.LISTENING)
        if e:
            events.append(e)

        return events

    # ── 이관 처리 ─────────────────────────────────────────────────────────────

    async def _trigger_transfer(
        self,
        reason: TransferReason,
        context_hint: str = "",
    ) -> list[OutboundEvent]:
        """TransferService 호출 및 결과에 따른 이벤트 반환."""
        context_summary = TransferService.build_context_summary(
            self.history, context_hint
        )
        transfer_req = TransferRequest(
            session_id=self.session_id,
            tenant_id=self.tenant_id,
            reason=reason,
            context_summary=context_summary,
            priority=_transfer_priority(reason),
        )
        result = await self._transfer_svc.request(
            fsm=self.fsm,
            transfer_req=transfer_req,
            fallback=TransferFallback.CALLBACK,
        )

        events: list[OutboundEvent] = [
            self._evt(
                "transfer_update",
                status=result.status.value,
                message=result.message,
                agent=result.agent_name or result.agent_id,
            )
        ]

        if result.status == TransferStatus.ACCEPTED:
            # 이관 완료 → 세션 종료 (단일 종료 경로)
            events.extend(await self.end_session(reason="transfer_accepted"))
        elif result.fallback_action == TransferFallback.AI_RESUME:
            # TRANSFER_FAILED → LISTENING 복귀 이벤트
            events.append(self._state_evt())

        return events

    async def _check_auto_transfer(self) -> list[OutboundEvent]:
        """연속 실패 임계치 초과 시 자동 이관 트리거."""
        if self._pipeline_failure_count >= PIPELINE_FAILURE_THRESHOLD:
            self.fsm.record_event(
                SessionEventType.VENDOR_DEGRADED,
                metadata={"failure_count": self._pipeline_failure_count},
            )
            return await self._trigger_transfer(
                reason=TransferReason.REPEATED_FAILURE,
                context_hint="AI 서비스 연속 장애로 인한 자동 이관",
            )
        return []

    # ── 영속성 ───────────────────────────────────────────────────────────────

    async def _persist_turn(
        self,
        user_text: str,
        ai_text: str,
        latency: dict,
        policy_level: str,
    ) -> None:
        """파이프라인 1턴 완료 후 MongoDB + Redis 저장 (실패해도 통화 유지)."""
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
            logger.warning("Failed to persist turn: %s", exc,
                           session_id=self.session_id)


# ── 세션 복구 / 신규 생성 팩토리 ──────────────────────────────────────────────

async def restore_or_create_orchestrator(
    session_id: str,
    tenant_id: str,
    client_id: str,
    repo: SessionRepository,
    policy_level: PolicyLevel = PolicyLevel.G1,
) -> tuple[CallSessionOrchestrator | None, list[OutboundEvent]]:
    """
    Redis hot-state → MongoDB cold-state 순서로 세션 복구를 시도한다.

    Returns:
        (orchestrator, initial_events) — 신규/복구 세션
        (None, [])                     — ENDED 세션, 연결 거부

    초기 이벤트:
        신규 세션   → [connected]
        재연결 세션 → [reconnected]
        ENDED 세션  → [] (호출자가 4004로 연결 거부)
    """
    hot_state = await repo.restore_hot_state(session_id)

    # ── 신규 세션 ────────────────────────────────────────────────────────
    if hot_state is None:
        fsm = SessionFSM(SessionState.IDLE)
        await repo.create({
            "session_id": session_id,
            "tenant_id":  tenant_id,
            "client_id":  client_id,
            "status":     SessionState.IDLE.value,
        })
        orchestrator = CallSessionOrchestrator(
            session_id=session_id,
            tenant_id=tenant_id,
            client_id=client_id,
            repo=repo,
            fsm=fsm,
            policy_level=policy_level,
        )
        initial = [OutboundEvent("connected",
                                 {"session_id": session_id, "reconnected": False})]
        return orchestrator, initial

    # ── ENDED 세션: 연결 거부 ─────────────────────────────────────────────
    if hot_state.get("status") == SessionState.ENDED.value:
        return None, []

    # ── 재연결 복구 ───────────────────────────────────────────────────────
    fsm_snapshot = hot_state.get("fsm_snapshot", {"state": "IDLE", "events": []})
    try:
        fsm = SessionFSM.from_snapshot(fsm_snapshot)
        # 재연결 직후 불안정 상태를 LISTENING으로 안전 복귀
        _SAFE_STATES = {SessionState.IDLE, SessionState.LISTENING, SessionState.ENDED}
        if fsm.state not in _SAFE_STATES and fsm.can_transition(SessionState.LISTENING):
            fsm.transition(SessionState.LISTENING)
    except Exception as exc:  # noqa: BLE001
        logger.warning("FSM snapshot restore failed, using IDLE: %s", exc,
                       session_id=session_id)
        fsm = SessionFSM(SessionState.IDLE)

    history: list[dict] = hot_state.get("history", [])
    turns_restored = len(history) // 2

    orchestrator = CallSessionOrchestrator(
        session_id=session_id,
        tenant_id=tenant_id,
        client_id=client_id,
        repo=repo,
        fsm=fsm,
        history=history,
        policy_level=policy_level,
    )

    logger.info("Session reconnected",
                session_id=session_id,
                turns_restored=turns_restored,
                state=fsm.state.value)

    initial = [OutboundEvent("reconnected", {
        "session_id":    session_id,
        "turns_restored": turns_restored,
        "state":          fsm.state.value,
    })]
    return orchestrator, initial
