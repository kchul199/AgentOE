"""
Transfer Service — AI 상담사 → 인간 상담사 이관 오케스트레이터

이관 흐름:
  1. AI가 이관 필요 판단 (G4/G5 정책, 고객 요청, 반복 실패)
  2. TransferService.request() 호출
     - FSM: current → TRANSFER_REQUESTED
     - MongoDB: 이관 요청 기록
     - CTI 큐에 이관 이벤트 발행 (현재: 로컬 이벤트, 추후 CTI 커넥터 연동)
  3. CTI 응답 대기 (기본 30초 타임아웃)
     - 수락: FSM → TRANSFER_ACCEPTED → 세션 종료 처리
     - 거절/타임아웃: FSM → TRANSFER_FAILED → AI 재응대 또는 ENDED
  4. 이관 실패 시 Fallback 전략:
     - RETRY: 큐에 재시도 (최대 3회)
     - CALLBACK: 콜백 예약 안내 후 세션 종료
     - AI_RESUME: AI가 계속 응대

이관 사유:
  - G4_POLICY: G4 정책 등급 도달 (금전/민감 업무)
  - G5_POLICY: G5 정책 등급 도달 (법무 제한)
  - CUSTOMER_REQUEST: 고객이 직접 요청 ("상담사 연결해줘")
  - REPEATED_FAILURE: AI 연속 실패 (파이프라인 오류 3회 이상)
  - TOOL_TIMEOUT: Tool 타임아웃 임계치 초과
  - MANUAL: 운영자 수동 이관
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from app.domain.session_fsm import SessionFSM, SessionEventType, SessionState
from app.repositories.session_repository import SessionRepository

logger = logging.getLogger(__name__)

# 이관 응답 대기 타임아웃 (초)
TRANSFER_TIMEOUT_SECONDS = 30

# 이관 재시도 최대 횟수
MAX_TRANSFER_RETRIES = 3


class TransferReason(str, Enum):
    G4_POLICY = "G4_POLICY"
    G5_POLICY = "G5_POLICY"
    CUSTOMER_REQUEST = "CUSTOMER_REQUEST"
    REPEATED_FAILURE = "REPEATED_FAILURE"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    MANUAL = "MANUAL"


class TransferFallback(str, Enum):
    RETRY = "RETRY"         # 큐 재시도
    CALLBACK = "CALLBACK"   # 콜백 예약 후 종료
    AI_RESUME = "AI_RESUME" # AI 계속 응대


class TransferStatus(str, Enum):
    PENDING = "PENDING"
    ACCEPTED = "ACCEPTED"
    FAILED = "FAILED"
    TIMED_OUT = "TIMED_OUT"
    FALLBACK_CALLBACK = "FALLBACK_CALLBACK"
    FALLBACK_AI_RESUME = "FALLBACK_AI_RESUME"


@dataclass
class TransferRequest:
    session_id: str
    tenant_id: str
    reason: TransferReason
    context_summary: str        # LLM이 생성한 상담 요약 (상담사에게 전달)
    policy_level: str = "G1"
    priority: int = 5           # 1(긴급) ~ 10(일반)
    metadata: dict = field(default_factory=dict)
    requested_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class TransferResult:
    status: TransferStatus
    agent_id: str | None = None       # 수락한 상담사 ID
    agent_name: str | None = None
    queue_position: int | None = None  # 대기 큐 위치 (FAILED 시)
    fallback_action: TransferFallback | None = None
    message: str = ""                  # 고객 안내 메시지
    completed_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TransferService:
    """
    상담사 이관 오케스트레이터.

    CTI 시스템 미연동 상태에서도 동작하도록 설계:
    - CTI 커넥터 미설치: Mock 응답 (테스트/개발 환경)
    - CTI 커넥터 설치: 실제 ACD 큐 연동
    """

    def __init__(
        self,
        session_repo: SessionRepository | None = None,
        cti_connector: Any | None = None,
    ) -> None:
        self._repo = session_repo or SessionRepository()
        self._cti = cti_connector  # None이면 Mock 모드

    # ── 이관 요청 (메인 진입점) ────────────────────────────────────────────────

    async def request(
        self,
        fsm: SessionFSM,
        transfer_req: TransferRequest,
        fallback: TransferFallback = TransferFallback.CALLBACK,
        retry_count: int = 0,
    ) -> TransferResult:
        """
        상담사 이관 요청 처리.

        Args:
            fsm: 현재 세션 FSM (상태 전이 직접 수행)
            transfer_req: 이관 요청 정보
            fallback: 이관 실패 시 처리 전략
            retry_count: 현재 재시도 횟수

        Returns:
            TransferResult: 이관 결과
        """
        session_id = transfer_req.session_id

        logger.info(
            "Transfer requested",
            extra={
                "session_id": session_id,
                "reason": transfer_req.reason.value,
                "retry": retry_count,
            },
        )

        # 1. FSM → TRANSFER_REQUESTED
        try:
            fsm.transition(
                SessionState.TRANSFER_REQUESTED,
                metadata={
                    "reason": transfer_req.reason.value,
                    "priority": transfer_req.priority,
                    "retry_count": retry_count,
                },
            )
        except ValueError as exc:
            logger.error("FSM transition failed for transfer: %s", exc)
            return TransferResult(
                status=TransferStatus.FAILED,
                message="세션 상태로 인해 이관이 불가능합니다.",
            )

        # 2. MongoDB에 이관 요청 저장
        await self._repo.save_transfer_info(
            session_id,
            {
                "reason": transfer_req.reason.value,
                "context_summary": transfer_req.context_summary,
                "policy_level": transfer_req.policy_level,
                "priority": transfer_req.priority,
                "retry_count": retry_count,
                "requested_at": transfer_req.requested_at.isoformat(),
                "status": TransferStatus.PENDING.value,
            },
        )
        await self._repo.update_state(
            session_id,
            SessionState.TRANSFER_REQUESTED.value,
            fsm=fsm,
        )

        # 3. CTI 큐에 이관 이벤트 발행
        result = await self._dispatch_to_cti(transfer_req)

        # 4. 결과 처리
        if result.status == TransferStatus.ACCEPTED:
            return await self._on_transfer_accepted(fsm, session_id, result)

        # 5. 실패 처리 — fallback 전략 적용
        return await self._on_transfer_failed(
            fsm=fsm,
            session_id=session_id,
            result=result,
            transfer_req=transfer_req,
            fallback=fallback,
            retry_count=retry_count,
        )

    # ── CTI 큐 발행 ────────────────────────────────────────────────────────────

    async def _dispatch_to_cti(self, req: TransferRequest) -> TransferResult:
        """
        CTI 시스템에 이관 이벤트 발행.
        CTI 커넥터 미연동 시 Mock 응답 반환.
        """
        if self._cti is None:
            return await self._mock_cti_dispatch(req)

        try:
            cti_response = await asyncio.wait_for(
                self._cti.transfer(
                    session_id=req.session_id,
                    tenant_id=req.tenant_id,
                    reason=req.reason.value,
                    context=req.context_summary,
                    priority=req.priority,
                ),
                timeout=TRANSFER_TIMEOUT_SECONDS,
            )
            return self._parse_cti_response(cti_response)

        except asyncio.TimeoutError:
            logger.warning(
                "CTI transfer timed out",
                extra={"session_id": req.session_id},
            )
            return TransferResult(
                status=TransferStatus.TIMED_OUT,
                message="상담사 연결 시간이 초과되었습니다.",
            )
        except Exception as exc:  # noqa: BLE001
            logger.error(
                "CTI dispatch error: %s",
                exc,
                extra={"session_id": req.session_id},
            )
            return TransferResult(
                status=TransferStatus.FAILED,
                message="상담사 시스템 오류가 발생했습니다.",
            )

    async def _mock_cti_dispatch(self, req: TransferRequest) -> TransferResult:
        """
        CTI 미연동 환경용 Mock 응답.
        G4/G5 이관 요청 → 항상 실패 반환 (콜백 fallback으로 유도).
        개발/테스트 환경에서 전체 이관 흐름 검증 가능.
        """
        await asyncio.sleep(0)  # 비동기 yield
        logger.debug(
            "CTI Mock: returning FAILED (no CTI connector)",
            extra={"session_id": req.session_id},
        )
        return TransferResult(
            status=TransferStatus.FAILED,
            queue_position=None,
            message="[MOCK] 현재 가용 상담사가 없습니다.",
        )

    def _parse_cti_response(self, response: Any) -> TransferResult:
        """CTI 커넥터 응답을 TransferResult로 변환."""
        if isinstance(response, dict):
            status = TransferStatus(response.get("status", "FAILED"))
            return TransferResult(
                status=status,
                agent_id=response.get("agent_id"),
                agent_name=response.get("agent_name"),
                queue_position=response.get("queue_position"),
                message=response.get("message", ""),
            )
        return TransferResult(status=TransferStatus.FAILED, message="CTI 응답 파싱 실패")

    # ── 결과 처리 ──────────────────────────────────────────────────────────────

    async def _on_transfer_accepted(
        self,
        fsm: SessionFSM,
        session_id: str,
        result: TransferResult,
    ) -> TransferResult:
        """이관 수락 처리: FSM → TRANSFER_ACCEPTED → ENDED."""
        fsm.transition(
            SessionState.TRANSFER_ACCEPTED,
            metadata={
                "agent_id": result.agent_id,
                "agent_name": result.agent_name,
            },
        )
        await self._repo.update_state(
            session_id,
            SessionState.TRANSFER_ACCEPTED.value,
            extra={"transfer_info.status": TransferStatus.ACCEPTED.value},
            fsm=fsm,
        )
        logger.info(
            "Transfer accepted",
            extra={
                "session_id": session_id,
                "agent_id": result.agent_id,
            },
        )
        result.message = (
            f"상담사 {result.agent_name or result.agent_id}님께 연결됩니다. "
            "잠시만 기다려 주세요."
        )
        return result

    async def _on_transfer_failed(
        self,
        fsm: SessionFSM,
        session_id: str,
        result: TransferResult,
        transfer_req: TransferRequest,
        fallback: TransferFallback,
        retry_count: int,
    ) -> TransferResult:
        """이관 실패 처리 — fallback 전략 실행."""

        # RETRY: 최대 재시도 횟수 미만이면 재시도
        if fallback == TransferFallback.RETRY and retry_count < MAX_TRANSFER_RETRIES:
            logger.info(
                "Transfer retry %d/%d",
                retry_count + 1,
                MAX_TRANSFER_RETRIES,
                extra={"session_id": session_id},
            )
            # FSM을 LISTENING으로 복귀시키고 재시도
            fsm.transition(SessionState.TRANSFER_FAILED)
            fsm.transition(SessionState.LISTENING)
            return await self.request(
                fsm=fsm,
                transfer_req=transfer_req,
                fallback=fallback,
                retry_count=retry_count + 1,
            )

        # FSM → TRANSFER_FAILED
        fsm.transition(
            SessionState.TRANSFER_FAILED,
            metadata={"fallback": fallback.value, "reason": result.status.value},
        )

        if fallback == TransferFallback.CALLBACK:
            result = await self._handle_callback_fallback(fsm, session_id, result)
        elif fallback == TransferFallback.AI_RESUME:
            result = await self._handle_ai_resume_fallback(fsm, session_id, result)
        else:
            # 기본: ENDED
            fsm.transition(SessionState.ENDED)
            await self._repo.end_session(session_id, reason="transfer_failed_no_fallback", fsm=fsm)
            result.message = "죄송합니다. 현재 상담사 연결이 어렵습니다. 나중에 다시 연락 주시기 바랍니다."

        return result

    async def _handle_callback_fallback(
        self,
        fsm: SessionFSM,
        session_id: str,
        result: TransferResult,
    ) -> TransferResult:
        """콜백 예약 안내 후 세션 종료."""
        fsm.record_event(
            SessionEventType.CALLBACK_SCHEDULED,
            metadata={"reason": "transfer_fallback"},
        )
        await self._repo.update_state(
            session_id,
            SessionState.TRANSFER_FAILED.value,
            extra={"transfer_info.status": TransferStatus.FALLBACK_CALLBACK.value},
            fsm=fsm,
        )
        result.status = TransferStatus.FALLBACK_CALLBACK
        result.fallback_action = TransferFallback.CALLBACK
        result.message = (
            "현재 모든 상담사가 통화 중입니다. "
            "고객님의 연락처로 빠른 시간 내에 다시 연락드리겠습니다."
        )
        logger.info("Transfer fallback: CALLBACK", extra={"session_id": session_id})
        return result

    async def _handle_ai_resume_fallback(
        self,
        fsm: SessionFSM,
        session_id: str,
        result: TransferResult,
    ) -> TransferResult:
        """AI 재응대 복귀."""
        fsm.transition(
            SessionState.LISTENING,
            metadata={"reason": "transfer_failed_ai_resume"},
        )
        await self._repo.update_state(
            session_id,
            SessionState.LISTENING.value,
            extra={"transfer_info.status": TransferStatus.FALLBACK_AI_RESUME.value},
            fsm=fsm,
        )
        result.status = TransferStatus.FALLBACK_AI_RESUME
        result.fallback_action = TransferFallback.AI_RESUME
        result.message = (
            "현재 상담사 연결이 어렵습니다. "
            "제가 계속 도와드리겠습니다. 어떤 도움이 필요하신가요?"
        )
        logger.info("Transfer fallback: AI_RESUME", extra={"session_id": session_id})
        return result

    # ── 고객 요청 감지 헬퍼 ────────────────────────────────────────────────────

    @staticmethod
    def detect_transfer_intent(text: str) -> bool:
        """
        고객 발화에서 상담사 이관 의도 감지.
        간단한 키워드 기반 (추후 NLU 모델로 교체 가능).
        """
        TRANSFER_KEYWORDS = [
            "상담사", "담당자", "사람", "직원", "연결", "바꿔",
            "agent", "human", "operator", "representative",
            "사람이랑", "사람과", "직접", "전화", "통화",
        ]
        text_lower = text.lower()
        return any(kw in text_lower for kw in TRANSFER_KEYWORDS)

    @staticmethod
    def build_context_summary(history: list[dict], current_issue: str = "") -> str:
        """
        대화 히스토리를 상담사 인계 요약문으로 압축.
        실제 운영 시 LLM 요약으로 교체 권장.
        """
        if not history:
            return current_issue or "고객 문의 내용 없음"

        turns = []
        for i, msg in enumerate(history[-10:]):  # 최근 5턴
            role = "고객" if msg["role"] == "user" else "AI"
            turns.append(f"{role}: {msg['content'][:100]}")

        summary = "\n".join(turns)
        if current_issue:
            summary = f"현재 문의: {current_issue}\n\n최근 대화:\n{summary}"
        return summary
