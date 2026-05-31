"""Track 2-e: 로그 context 바인딩/해제의 대칭성 검증.

핵심 시나리오:
  1. scoped_context 는 블록을 나갈 때 자신이 바인딩한 키만 풀어야 한다
     (상위 스코프 훼손 금지).
  2. unbind_request_context 는 전체를 비운다.
  3. 예외가 발생해도 scoped_context 는 반드시 unbind 한다.
"""

from __future__ import annotations

import pytest
from structlog.contextvars import get_contextvars

from app.core.logging import (
    bind_session_context,
    scoped_context,
    unbind_request_context,
)


def _current_ctx() -> dict:
    return dict(get_contextvars())


def setup_function(_fn) -> None:
    # 각 테스트 시작 시 깨끗한 상태 보장
    unbind_request_context()


def test_scoped_context_binds_and_unbinds() -> None:
    assert _current_ctx() == {}
    with scoped_context(session_id="s1", tenant_id="t1"):
        ctx = _current_ctx()
        assert ctx["session_id"] == "s1"
        assert ctx["tenant_id"] == "t1"
    # 블록 이탈 후 풀려야 함
    assert _current_ctx() == {}


def test_scoped_context_preserves_outer_scope() -> None:
    # 상위 스코프 — WS 레벨 바인딩 시뮬레이션
    bind_session_context(session_id="outer", tenant_id="T")
    with scoped_context(pipeline_stage="stt"):
        ctx = _current_ctx()
        assert ctx["session_id"] == "outer"
        assert ctx["pipeline_stage"] == "stt"

    # 블록 이탈: 내부 바인딩만 풀려야 하며 상위(session_id)는 유지
    ctx = _current_ctx()
    assert ctx.get("session_id") == "outer"
    assert "pipeline_stage" not in ctx


def test_scoped_context_on_exception() -> None:
    bind_session_context(session_id="outer")
    with pytest.raises(RuntimeError), scoped_context(pipeline_stage="llm"):
        raise RuntimeError("boom")
    # 예외 경로에서도 반드시 unbind
    ctx = _current_ctx()
    assert "pipeline_stage" not in ctx
    assert ctx.get("session_id") == "outer"


def test_unbind_request_context_clears_all() -> None:
    bind_session_context(session_id="s", tenant_id="t")
    assert _current_ctx() != {}
    unbind_request_context()
    assert _current_ctx() == {}


def test_scoped_context_ignores_none_values() -> None:
    # None 값은 바인딩하지 않음 → 이후 unbind 대상에서도 제외
    with scoped_context(session_id="s1", tenant_id=None):
        ctx = _current_ctx()
        assert ctx.get("session_id") == "s1"
        assert "tenant_id" not in ctx
    assert _current_ctx() == {}
