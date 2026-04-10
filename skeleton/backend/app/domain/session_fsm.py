"""Session Finite State Machine (FSM) for voice call lifecycle."""
from enum import StrEnum
from typing import ClassVar


class SessionState(StrEnum):
    IDLE = "IDLE"
    LISTENING = "LISTENING"
    SPEAKING_DETECTED = "SPEAKING_DETECTED"
    PROCESSING = "PROCESSING"
    INFERRING = "INFERRING"
    RESPONDING = "RESPONDING"
    ENDED = "ENDED"


class SessionFSM:
    """Validates and executes session state transitions."""

    VALID_TRANSITIONS: ClassVar[dict[SessionState, set[SessionState]]] = {
        SessionState.IDLE: {SessionState.LISTENING, SessionState.ENDED},
        SessionState.LISTENING: {SessionState.SPEAKING_DETECTED, SessionState.IDLE, SessionState.ENDED},
        SessionState.SPEAKING_DETECTED: {SessionState.PROCESSING, SessionState.LISTENING, SessionState.ENDED},
        SessionState.PROCESSING: {SessionState.INFERRING, SessionState.LISTENING, SessionState.ENDED},
        SessionState.INFERRING: {SessionState.RESPONDING, SessionState.LISTENING, SessionState.ENDED},
        SessionState.RESPONDING: {SessionState.LISTENING, SessionState.IDLE, SessionState.ENDED},
        SessionState.ENDED: set(),
    }

    def __init__(self, initial_state: SessionState = SessionState.IDLE) -> None:
        self.state = initial_state

    def can_transition(self, to: SessionState) -> bool:
        return to in self.VALID_TRANSITIONS.get(self.state, set())

    def transition(self, to: SessionState) -> SessionState:
        """Perform state transition. Raises ValueError if invalid."""
        if not self.can_transition(to):
            raise ValueError(
                f"Invalid transition: {self.state} → {to}. "
                f"Allowed: {self.VALID_TRANSITIONS.get(self.state, set())}"
            )
        previous = self.state
        self.state = to
        return previous
