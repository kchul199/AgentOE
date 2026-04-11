"""
Session Finite State Machine (FSM) for voice call lifecycle.

상태 전이 다이어그램:

  IDLE ──────────────────────────────────────────┐
    │                                             │
    ▼                                             │
  LISTENING ◄─────── RESPONDING                  │
    │                    ▲                        │
    ▼                    │                        │
  SPEAKING_DETECTED      │                        │
    │                    │                        │
    ▼                    │                        │
  PROCESSING             │                        │
    │                    │                        │
    ▼                    │                        │
  INFERRING ─────────────┘                        │
                                                  │
  Any state ──► TRANSFER_REQUESTED                │
                     │                            │
          ┌──────────┴──────────┐                 │
          ▼                     ▼                 │
  TRANSFER_ACCEPTED    TRANSFER_FAILED            │
          │                     │                 │
          ▼                     ▼                 │
        ENDED              LISTENING              │
                                                  │
  Any state (except ENDED) ──► ENDED ◄────────────┘

  운영 이벤트 (FSM 상태 외 오버레이):
    CALLBACK_SCHEDULED — 세션 종료 없이 콜백 예약됨을 표시
    TOOL_TIMEOUT       — Tool 호출 시간 초과 감지
    POLICY_BLOCKED     — PolicyGate 차단 감지
    KILL_SWITCH_TRIGGERED — Kill Switch 발동 감지
    VENDOR_DEGRADED    — AI 벤더 장애 감지
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import ClassVar


class SessionState(str, Enum):
    # 핵심 통화 상태
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    SPEAKING_DETECTED = "SPEAKING_DETECTED"
    PROCESSING = "PROCESSING"
    INFERRING = "INFERRING"
    RESPONDING = "RESPONDING"

    # 상담사 이관 상태
    TRANSFER_REQUESTED = "TRANSFER_REQUESTED"   # AI→상담사 이관 요청됨
    TRANSFER_ACCEPTED = "TRANSFER_ACCEPTED"     # 상담사 수락 — 이관 완료
    TRANSFER_FAILED = "TRANSFER_FAILED"         # 이관 실패 (상담사 없음 등)

    # 종료
    ENDED = "ENDED"


class SessionEventType(str, Enum):
    """FSM 상태 전이가 아닌 오버레이 이벤트 (운영 감사용)."""
    STATE_CHANGED = "STATE_CHANGED"
    CALLBACK_SCHEDULED = "CALLBACK_SCHEDULED"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    POLICY_BLOCKED = "POLICY_BLOCKED"
    KILL_SWITCH_TRIGGERED = "KILL_SWITCH_TRIGGERED"
    VENDOR_DEGRADED = "VENDOR_DEGRADED"
    SESSION_RESTORED = "SESSION_RESTORED"       # 재연결 시 복구
    TRANSFER_QUEUED = "TRANSFER_QUEUED"         # CTI 큐에 전달됨


@dataclass
class SessionEvent:
    """단일 FSM 이벤트 기록."""
    event_type: SessionEventType
    from_state: SessionState | None
    to_state: SessionState | None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict = field(default_factory=dict)


class SessionFSM:
    """
    음성 콜봇 세션 상태 머신.

    특징:
    - 이벤트 히스토리를 내부에 유지 (재연결 복구 및 감사용)
    - TRANSFER_REQUESTED는 어느 상태에서도 진입 가능 (긴급 이관)
    - TRANSFER_FAILED → LISTENING 으로 자동 폴백 경로 제공
    """

    VALID_TRANSITIONS: ClassVar[dict[SessionState, set[SessionState]]] = {
        SessionState.IDLE: {
            SessionState.LISTENING,
            SessionState.ENDED,
        },
        SessionState.LISTENING: {
            SessionState.SPEAKING_DETECTED,
            SessionState.IDLE,
            SessionState.TRANSFER_REQUESTED,
            SessionState.ENDED,
        },
        SessionState.SPEAKING_DETECTED: {
            SessionState.PROCESSING,
            SessionState.LISTENING,
            SessionState.TRANSFER_REQUESTED,
            SessionState.ENDED,
        },
        SessionState.PROCESSING: {
            SessionState.INFERRING,
            SessionState.LISTENING,
            SessionState.TRANSFER_REQUESTED,
            SessionState.ENDED,
        },
        SessionState.INFERRING: {
            SessionState.RESPONDING,
            SessionState.LISTENING,
            SessionState.TRANSFER_REQUESTED,
            SessionState.ENDED,
        },
        SessionState.RESPONDING: {
            SessionState.LISTENING,
            SessionState.IDLE,
            SessionState.TRANSFER_REQUESTED,
            SessionState.ENDED,
        },
        # 이관 상태 전이
        SessionState.TRANSFER_REQUESTED: {
            SessionState.TRANSFER_ACCEPTED,
            SessionState.TRANSFER_FAILED,
            SessionState.ENDED,
        },
        SessionState.TRANSFER_ACCEPTED: {
            SessionState.ENDED,
        },
        SessionState.TRANSFER_FAILED: {
            SessionState.LISTENING,   # 이관 실패 → AI가 다시 응대
            SessionState.ENDED,
        },
        SessionState.ENDED: set(),
    }

    def __init__(self, initial_state: SessionState = SessionState.IDLE) -> None:
        self.state = initial_state
        self._events: list[SessionEvent] = []

    # ── 상태 전이 ──────────────────────────────────────────────────────────────

    def can_transition(self, to: SessionState) -> bool:
        return to in self.VALID_TRANSITIONS.get(self.state, set())

    def transition(
        self,
        to: SessionState,
        metadata: dict | None = None,
    ) -> SessionState:
        """
        상태 전이 실행. 이벤트 히스토리에 기록.
        Raises ValueError if invalid.
        """
        if not self.can_transition(to):
            raise ValueError(
                f"Invalid transition: {self.state} → {to}. "
                f"Allowed: {self.VALID_TRANSITIONS.get(self.state, set())}"
            )
        previous = self.state
        self.state = to
        self._events.append(SessionEvent(
            event_type=SessionEventType.STATE_CHANGED,
            from_state=previous,
            to_state=to,
            metadata=metadata or {},
        ))
        return previous

    # ── 오버레이 이벤트 기록 (상태 변경 없음) ──────────────────────────────────

    def record_event(
        self,
        event_type: SessionEventType,
        metadata: dict | None = None,
    ) -> None:
        """FSM 상태 전이 없이 운영 이벤트를 히스토리에 추가."""
        self._events.append(SessionEvent(
            event_type=event_type,
            from_state=self.state,
            to_state=None,
            metadata=metadata or {},
        ))

    # ── 직렬화 / 역직렬화 (재연결 복구용) ─────────────────────────────────────

    def to_snapshot(self) -> dict:
        """Redis/MongoDB 저장용 스냅샷 직렬화."""
        return {
            "state": self.state.value,
            "events": [
                {
                    "event_type": e.event_type.value,
                    "from_state": e.from_state.value if e.from_state else None,
                    "to_state": e.to_state.value if e.to_state else None,
                    "timestamp": e.timestamp.isoformat(),
                    "metadata": e.metadata,
                }
                for e in self._events[-50:]  # 최근 50개만 저장
            ],
        }

    @classmethod
    def from_snapshot(cls, snapshot: dict) -> "SessionFSM":
        """Redis/MongoDB 스냅샷에서 FSM 복원."""
        state = SessionState(snapshot.get("state", SessionState.IDLE.value))
        fsm = cls(initial_state=state)
        for raw in snapshot.get("events", []):
            fsm._events.append(SessionEvent(
                event_type=SessionEventType(raw["event_type"]),
                from_state=SessionState(raw["from_state"]) if raw.get("from_state") else None,
                to_state=SessionState(raw["to_state"]) if raw.get("to_state") else None,
                timestamp=datetime.fromisoformat(raw["timestamp"]),
                metadata=raw.get("metadata", {}),
            ))
        fsm.record_event(SessionEventType.SESSION_RESTORED, {"restored_from": "snapshot"})
        return fsm

    # ── 헬퍼 프로퍼티 ──────────────────────────────────────────────────────────

    @property
    def events(self) -> list[SessionEvent]:
        return list(self._events)

    @property
    def is_transfer_in_progress(self) -> bool:
        return self.state in (
            SessionState.TRANSFER_REQUESTED,
            SessionState.TRANSFER_ACCEPTED,
        )

    @property
    def is_active(self) -> bool:
        """통화가 아직 진행 중인지 여부."""
        return self.state != SessionState.ENDED

    def __repr__(self) -> str:
        return f"SessionFSM(state={self.state.value}, events={len(self._events)})"
