"""Unit tests for Session FSM."""

import pytest

from app.domain.session_fsm import SessionFSM, SessionState


def test_initial_state():
    fsm = SessionFSM()
    assert fsm.state == SessionState.IDLE


def test_valid_transitions():
    fsm = SessionFSM()
    fsm.transition(SessionState.LISTENING)
    assert fsm.state == SessionState.LISTENING
    fsm.transition(SessionState.SPEAKING_DETECTED)
    assert fsm.state == SessionState.SPEAKING_DETECTED


def test_invalid_transition_raises():
    fsm = SessionFSM()
    with pytest.raises(ValueError, match="Invalid transition"):
        fsm.transition(SessionState.RESPONDING)


def test_ended_state_no_transitions():
    fsm = SessionFSM(initial_state=SessionState.ENDED)
    assert not fsm.can_transition(SessionState.IDLE)
    assert not fsm.can_transition(SessionState.LISTENING)


def test_full_happy_path():
    fsm = SessionFSM()
    transitions = [
        SessionState.LISTENING,
        SessionState.SPEAKING_DETECTED,
        SessionState.PROCESSING,
        SessionState.INFERRING,
        SessionState.RESPONDING,
        SessionState.IDLE,
        SessionState.ENDED,
    ]
    for state in transitions:
        fsm.transition(state)
    assert fsm.state == SessionState.ENDED
