"""
AI Pipeline Orchestrator
STT → PolicyGate → LLM → TTS 전체 파이프라인 조율

Sprint 4 추가:
  - 메트릭 레코딩: 각 단계별 레이턴시 + 성공/실패 카운터
  - Degraded Mode: 벤더 장애 단계별 fallback 텍스트 응답
  - structlog context var 파이프라인 단계 바인딩

Degraded Mode Matrix:
  STT 장애  → 고정 안내문구 STT 텍스트로 대체 ("잠시 후 다시 말씀해 주세요")
  LLM 장애  → 기본 응답 텍스트로 대체 (시나리오별 fallback)
  TTS 장애  → 텍스트 응답만 반환 (audio_bytes=None, degraded=True)
  전체 장애 → 이관 트리거용 "DEGRADED" 플래그 반환
"""

from __future__ import annotations

import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import structlog

from app.core.logging import bind_pipeline_context, unbind_keys
from app.core.metrics import record_pipeline_call
from app.domain.circuit_breaker import CircuitBreakerOpenError, get_all_statuses
from app.domain.policy_gate import PolicyGate, PolicyLevel
from app.domain.session_fsm import SessionEventType, SessionFSM, SessionState
from app.services.llm_service import LLMChunk, LLMService
from app.services.stt_service import STTService
from app.services.tts_service import TTSService

logger = structlog.get_logger(__name__)

# ── 레이턴시 예산 (P95 목표, ms) ──────────────────────────────────────────────
LATENCY_BUDGET = {
    "stt_ms": 500,
    "llm_first_token_ms": 500,
    "tts_ms": 300,
    "total_ms": 2500,
}

# ── Degraded Mode 한국어 Fallback 메시지 ──────────────────────────────────────
DEGRADED_MESSAGES = {
    "stt_unavailable": (
        "죄송합니다, 음성 인식 서비스가 일시적으로 응답하지 않습니다. "
        "잠시 후 다시 말씀해 주시겠어요?"
    ),
    "llm_unavailable": (
        "죄송합니다, AI 응답 생성 서비스가 일시적으로 응답하지 않습니다. "
        "잠시만 기다려 주시면 곧 정상화됩니다."
    ),
    "tts_unavailable": (
        # TTS만 장애 시 텍스트 응답은 정상 — audio_bytes만 None 반환
        ""
    ),
    "all_unavailable": (
        "현재 AI 서비스에 장애가 발생하여 상담사로 연결해 드리겠습니다. 잠시만 기다려 주세요."
    ),
}


@dataclass
class PipelineResult:
    stt_text: str
    llm_text: str
    tts_audio: bytes | None
    policy_level: str
    policy_allowed: bool
    latency: dict[str, float]
    filler_triggered: bool = False
    degraded: bool = False  # True이면 벤더 장애로 fallback 응답
    degraded_stage: str | None = None  # "stt" / "llm" / "tts" / "all"


class AIPipeline:
    """
    음성 콜봇 AI 파이프라인.
    LISTENING → STT → PolicyGate → LLM → TTS → RESPONDING

    장애 전략:
      - 각 단계 CircuitBreakerOpenError → Degraded Mode fallback
      - FSM 이벤트 기록 (VENDOR_DEGRADED)
      - 메트릭 카운터 업데이트
    """

    def __init__(self) -> None:
        self._stt = STTService()
        self._llm = LLMService()
        self._tts = TTSService()
        self._policy = PolicyGate()

    async def process(
        self,
        audio_bytes: bytes,
        session_id: str,
        tenant_id: str,
        fsm: SessionFSM,
        history: list[dict] | None = None,
        policy_level: PolicyLevel = PolicyLevel.G1,
        system_prompt: str | None = None,
        auth_state: dict | None = None,
    ) -> PipelineResult:
        """전체 파이프라인 실행 — 단계별 Degraded Mode 포함."""
        latency: dict[str, float] = {}
        pipeline_start = time.monotonic()
        degraded = False
        degraded_stage: str | None = None

        # ── 1. STT ────────────────────────────────────────────────────────────
        bind_pipeline_context(stage="stt")
        fsm.transition(SessionState.PROCESSING)
        t0 = time.monotonic()

        try:
            stt_result = await self._stt.transcribe(audio_bytes)
            latency["stt_ms"] = (time.monotonic() - t0) * 1000
            stt_text = stt_result.text
            if latency["stt_ms"] > LATENCY_BUDGET["stt_ms"]:
                logger.warning(
                    "STT latency budget exceeded",
                    stt_ms=round(latency["stt_ms"], 1),
                    budget_ms=LATENCY_BUDGET["stt_ms"],
                )
        except (CircuitBreakerOpenError, Exception) as exc:
            latency["stt_ms"] = (time.monotonic() - t0) * 1000
            degraded, degraded_stage = True, "stt"
            stt_text = DEGRADED_MESSAGES["stt_unavailable"]
            fsm.record_event(
                SessionEventType.VENDOR_DEGRADED,
                {"stage": "stt", "error": str(exc)},
            )
            logger.error("STT unavailable — degraded response", error=str(exc))
            # STT 장애 시 LLM/TTS도 건너뜀
            total_ms = (time.monotonic() - pipeline_start) * 1000
            latency["total_ms"] = total_ms
            record_pipeline_call(
                tenant_id=tenant_id,
                success=False,
                total_ms=total_ms,
                degraded=True,
            )
            unbind_keys("pipeline_stage", "policy_level")
            return PipelineResult(
                stt_text="",
                llm_text=stt_text,
                tts_audio=None,
                policy_level=policy_level.value,
                policy_allowed=True,
                latency=latency,
                degraded=True,
                degraded_stage="stt",
            )

        # ── 2. PolicyGate ──────────────────────────────────────────────────────
        bind_pipeline_context(stage="policy")
        policy_eval = await self._policy.evaluate(
            action="llm_inference",
            level=policy_level,
            context={"session_id": session_id, "text": stt_text},
            session_auth_state=auth_state,
        )
        if not policy_eval.allowed:
            fsm.transition(SessionState.RESPONDING)
            total_ms = (time.monotonic() - pipeline_start) * 1000
            latency["total_ms"] = total_ms
            record_pipeline_call(
                tenant_id=tenant_id,
                success=True,
                total_ms=total_ms,
                stt_ms=latency.get("stt_ms", 0),
            )
            unbind_keys("pipeline_stage", "policy_level")
            return PipelineResult(
                stt_text=stt_text,
                llm_text=f"[POLICY_BLOCKED:{policy_eval.level.value}]",
                tts_audio=None,
                policy_level=policy_eval.level.value,
                policy_allowed=False,
                latency=latency,
            )

        # ── 3. LLM ────────────────────────────────────────────────────────────
        bind_pipeline_context(stage="llm", policy_level=policy_level.value)
        fsm.transition(SessionState.INFERRING)
        t1 = time.monotonic()

        try:
            llm_result = await self._llm.complete(
                stt_text, history=history, system_prompt=system_prompt
            )
            latency["llm_ms"] = (time.monotonic() - t1) * 1000
            llm_text = llm_result.full_text
            filler_triggered = llm_result.filler_triggered
            if latency["llm_ms"] > LATENCY_BUDGET["llm_first_token_ms"]:
                logger.warning(
                    "LLM latency budget exceeded",
                    llm_ms=round(latency["llm_ms"], 1),
                )
        except (CircuitBreakerOpenError, Exception) as exc:
            latency["llm_ms"] = (time.monotonic() - t1) * 1000
            degraded, degraded_stage = True, "llm"
            llm_text = DEGRADED_MESSAGES["llm_unavailable"]
            filler_triggered = False
            fsm.record_event(
                SessionEventType.VENDOR_DEGRADED,
                {"stage": "llm", "error": str(exc)},
            )
            logger.error("LLM unavailable — degraded response", error=str(exc))

        # ── 4. TTS ────────────────────────────────────────────────────────────
        bind_pipeline_context(stage="tts")
        fsm.transition(SessionState.RESPONDING)
        t2 = time.monotonic()
        audio_bytes_out: bytes | None = None

        if not degraded:
            try:
                tts_result = await self._tts.synthesize(llm_text)
                latency["tts_ms"] = (time.monotonic() - t2) * 1000
                audio_bytes_out = tts_result.audio_bytes
                if latency["tts_ms"] > LATENCY_BUDGET["tts_ms"]:
                    logger.warning(
                        "TTS latency budget exceeded",
                        tts_ms=round(latency["tts_ms"], 1),
                    )
            except (CircuitBreakerOpenError, Exception) as exc:
                latency["tts_ms"] = (time.monotonic() - t2) * 1000
                # TTS만 장애 — 텍스트 응답은 정상, audio만 없음
                degraded_stage = degraded_stage or "tts"
                fsm.record_event(
                    SessionEventType.VENDOR_DEGRADED,
                    {"stage": "tts", "error": str(exc)},
                )
                logger.warning("TTS unavailable — text-only response", error=str(exc))
        else:
            latency["tts_ms"] = 0.0

        # ── 5. 레이턴시 집계 + 메트릭 레코딩 ─────────────────────────────────
        total_ms = (time.monotonic() - pipeline_start) * 1000
        latency["total_ms"] = total_ms

        if total_ms > LATENCY_BUDGET["total_ms"]:
            logger.warning(
                "Total pipeline latency budget exceeded",
                total_ms=round(total_ms, 1),
                budget_ms=LATENCY_BUDGET["total_ms"],
            )

        record_pipeline_call(
            tenant_id=tenant_id,
            success=not degraded,
            total_ms=total_ms,
            stt_ms=latency.get("stt_ms", 0),
            llm_ms=latency.get("llm_ms", 0),
            tts_ms=latency.get("tts_ms", 0),
            degraded=degraded,
        )

        logger.info(
            "Pipeline complete",
            total_ms=round(total_ms, 1),
            stt_ms=round(latency.get("stt_ms", 0), 1),
            llm_ms=round(latency.get("llm_ms", 0), 1),
            tts_ms=round(latency.get("tts_ms", 0), 1),
            degraded=degraded,
            degraded_stage=degraded_stage,
        )

        # ── 6. context 누수 방지 (Track 2-e) ────────────────────────────────
        #   각 단계에서 bind 한 pipeline_stage / policy_level 는 turn 경계를
        #   넘지 않도록 여기서 정리한다. 호출자(orchestrator) 가 별도로
        #   session_id / tenant_id 를 관리하므로 그 키들은 건드리지 않는다.
        unbind_keys("pipeline_stage", "policy_level")

        return PipelineResult(
            stt_text=stt_text,
            llm_text=llm_text,
            tts_audio=audio_bytes_out,
            policy_level=policy_eval.level.value,
            policy_allowed=True,
            latency=latency,
            filler_triggered=filler_triggered,
            degraded=degraded,
            degraded_stage=degraded_stage,
        )

    async def stream_llm(
        self,
        text: str,
        history: list[dict] | None = None,
        system_prompt: str | None = None,
    ) -> AsyncIterator[LLMChunk]:
        """LLM 스트리밍 청크 방출 (WebSocket 실시간 전송용)."""
        async for chunk in self._llm.stream(text, history, system_prompt):
            yield chunk

    def get_health(self) -> dict:
        """모든 AI 서비스 Circuit Breaker 상태 반환."""
        return {
            "circuit_breakers": get_all_statuses(),
            "latency_budget": LATENCY_BUDGET,
            "degraded_messages": {k: bool(v) for k, v in DEGRADED_MESSAGES.items()},
        }
