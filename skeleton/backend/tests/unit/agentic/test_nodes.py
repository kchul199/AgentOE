"""노드 팩토리 단위 테스트 — 각 노드가 상태를 올바르게 변환하는지.

CLAUDE.md 원칙 준수 검증 포인트:
  - 모든 노드는 async (Awaitable[dict]) 반환
  - Tool/LLM 실패 시 fallback_triggered=True 로 graceful fallback
  - Latency 측정 필드 (turn_latency_ms) 가 기록됨
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agentic.nodes.branch_node import make_branch_dispatcher, make_branch_node
from app.agentic.nodes.context_node import make_context_node
from app.agentic.nodes.end_node import make_end_node
from app.agentic.nodes.intent_node import make_intent_node
from app.agentic.nodes.llm_node import make_llm_node
from app.agentic.nodes.tool_node import make_tool_node
from app.agentic.nodes.transfer_node import make_transfer_node
from app.agentic.nodes.wait_node import make_wait_node
from app.agentic.scenario_dsl import (
    BranchNode,
    BranchNodeConfig,
    ContextUpdateNodeConfig,
    Edge,
    EndNodeConfig,
    IntentNodeConfig,
    LLMNodeConfig,
    ToolNodeConfig,
    TransferNodeConfig,
    WaitNodeConfig,
)
from app.agentic.state import empty_state


# ── End / Wait / Context (순수함수에 가까움) ────────────────────────────────


class TestEndNode:
    async def test_sets_should_end(self) -> None:
        node = make_end_node(EndNodeConfig())
        out = await node(empty_state("t", "s", "sc"))
        assert out["should_end"] is True

    async def test_closing_message_produces_message(self) -> None:
        node = make_end_node(EndNodeConfig(closing_message="bye"))
        out = await node(empty_state("t", "s", "sc"))
        assert out["assistant_output"] == "bye"
        assert out["messages"][0]["content"] == "bye"
        assert out["messages"][0]["role"] == "assistant"


class TestWaitNode:
    async def test_wait_is_passthrough_signal(self) -> None:
        node = make_wait_node(WaitNodeConfig(timeout_s=3))
        out = await node(empty_state("t", "s", "sc"))
        assert out.get("next_node") is None


class TestContextNode:
    async def test_set_slots_merges(self) -> None:
        node = make_context_node(
            ContextUpdateNodeConfig(set_slots={"verified": True, "plan": "pro"})
        )
        state = empty_state("t", "s", "sc")
        state["slots"] = {"existing": 1}
        out = await node(state)
        assert out["slots"]["existing"] == 1
        assert out["slots"]["verified"] is True
        assert out["slots"]["plan"] == "pro"

    async def test_clear_slots_removes(self) -> None:
        node = make_context_node(
            ContextUpdateNodeConfig(clear_slots=["to_clear"])
        )
        state = empty_state("t", "s", "sc")
        state["slots"] = {"keep": "x", "to_clear": "y"}
        out = await node(state)
        assert "to_clear" not in out["slots"]
        assert out["slots"]["keep"] == "x"


# ── Branch dispatcher (순수 라우팅 함수) ────────────────────────────────────


class TestBranchDispatcher:
    def _make(self, mode: str = "intent", edges: list[Edge] | None = None) -> callable:
        node = BranchNode(id="router", config=BranchNodeConfig(mode=mode))
        edges = edges or [
            Edge.model_validate({"from": "router", "when": "billing", "to": "bn"}),
            Edge.model_validate({"from": "router", "when": "balance", "to": "bl"}),
            Edge.model_validate({"from": "router", "when": "default", "to": "fb"}),
        ]
        return make_branch_dispatcher(node, edges)

    def test_intent_match_returns_target(self) -> None:
        dispatch = self._make("intent")
        state = empty_state("t", "s", "sc")
        state["intent"] = {"intent": "billing", "confidence": 0.9, "slots": {}}
        assert dispatch(state) == "bn"

    def test_intent_no_match_falls_to_default(self) -> None:
        dispatch = self._make("intent")
        state = empty_state("t", "s", "sc")
        state["intent"] = {"intent": "weather", "confidence": 0.9, "slots": {}}
        assert dispatch(state) == "fb"

    def test_fallback_triggered_forces_default(self) -> None:
        """Tool 실패 등으로 fallback_triggered=True 이면 default 강제."""
        dispatch = self._make("intent")
        state = empty_state("t", "s", "sc")
        state["intent"] = {"intent": "billing", "confidence": 0.9, "slots": {}}
        state["fallback_triggered"] = True
        assert dispatch(state) == "fb"

    def test_slot_mode(self) -> None:
        node = BranchNode(
            id="router", config=BranchNodeConfig(mode="slot", slot_key="plan")
        )
        edges = [
            Edge.model_validate({"from": "router", "when": "pro", "to": "pro_flow"}),
            Edge.model_validate({"from": "router", "when": "default", "to": "free_flow"}),
        ]
        dispatch = make_branch_dispatcher(node, edges)

        state = empty_state("t", "s", "sc")
        state["slots"] = {"plan": "pro"}
        assert dispatch(state) == "pro_flow"

        state["slots"] = {"plan": "basic"}
        assert dispatch(state) == "free_flow"

    def test_expr_mode_contains(self) -> None:
        node = BranchNode(id="router", config=BranchNodeConfig(mode="expr"))
        edges = [
            Edge.model_validate(
                {"from": "router", "when": "contains:계좌", "to": "account"}
            ),
            Edge.model_validate({"from": "router", "when": "default", "to": "other"}),
        ]
        dispatch = make_branch_dispatcher(node, edges)

        state = empty_state("t", "s", "sc")
        state["user_input"] = "제 계좌 잔액 알려주세요"
        assert dispatch(state) == "account"

        state["user_input"] = "날씨 어때요"
        assert dispatch(state) == "other"

    def test_expr_mode_regex(self) -> None:
        node = BranchNode(id="router", config=BranchNodeConfig(mode="expr"))
        edges = [
            Edge.model_validate(
                {"from": "router", "when": r"regex:\d{3,}", "to": "has_number"}
            ),
            Edge.model_validate({"from": "router", "when": "default", "to": "no_num"}),
        ]
        dispatch = make_branch_dispatcher(node, edges)
        state = empty_state("t", "s", "sc")
        state["user_input"] = "계좌 번호 1234 입니다"
        assert dispatch(state) == "has_number"

    def test_branch_passthrough_returns_empty_dict(self) -> None:
        """passthrough 노드는 상태 변경 없음."""
        # async call은 필요하지만 구조만 확인
        import asyncio

        passthrough = make_branch_node(BranchNodeConfig())
        out = asyncio.run(passthrough(empty_state("t", "s", "sc")))
        assert out == {}


# ── Intent Node (LLM 응답 파싱 + graceful fallback) ────────────────────────


class TestIntentNode:
    async def test_empty_input_returns_unknown(self) -> None:
        client = AsyncMock()
        node = make_intent_node(
            IntentNodeConfig(labels=["a", "b"], threshold=0.3),
            lambda: client,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["intent"]["intent"] == "unknown"
        assert out["fallback_triggered"] is True
        # LLM 호출 안 됨
        client.chat.completions.create.assert_not_called()

    async def test_happy_path_classification(self) -> None:
        fake_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"intent": "billing", "confidence": 0.9, "slots": {"month": "4"}}'
                    )
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=fake_resp))
            )
        )
        node = make_intent_node(
            IntentNodeConfig(labels=["billing", "balance"], threshold=0.5),
            lambda: client,
        )
        state = empty_state("t", "s", "sc")
        state["user_input"] = "이번 달 청구서 좀 확인해 주세요"
        out = await node(state)
        assert out["intent"]["intent"] == "billing"
        assert out["intent"]["confidence"] == pytest.approx(0.9)
        # slots 가 merge 되었는지
        assert out["slots"]["month"] == "4"

    async def test_unknown_label_coerces_to_unknown(self) -> None:
        fake_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"intent": "not_in_labels", "confidence": 0.95}'
                    )
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=fake_resp))
            )
        )
        node = make_intent_node(
            IntentNodeConfig(labels=["a", "b"]),
            lambda: client,
        )
        state = empty_state("t", "s", "sc")
        state["user_input"] = "hi"
        out = await node(state)
        assert out["intent"]["intent"] == "unknown"

    async def test_threshold_unmet_falls_to_unknown(self) -> None:
        fake_resp = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content='{"intent": "billing", "confidence": 0.1}'
                    )
                )
            ]
        )
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(create=AsyncMock(return_value=fake_resp))
            )
        )
        node = make_intent_node(
            IntentNodeConfig(labels=["billing", "balance"], threshold=0.5),
            lambda: client,
        )
        state = empty_state("t", "s", "sc")
        state["user_input"] = "???"
        out = await node(state)
        assert out["intent"]["intent"] == "unknown"

    async def test_exception_yields_graceful_unknown(self) -> None:
        """LLM 클라이언트가 예외 던져도 CLAUDE.md 원칙대로 fallback."""
        client = SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=AsyncMock(side_effect=RuntimeError("boom"))
                )
            )
        )
        node = make_intent_node(
            IntentNodeConfig(labels=["a", "b"]),
            lambda: client,
        )
        state = empty_state("t", "s", "sc")
        state["user_input"] = "hi"
        out = await node(state)
        assert out["intent"]["intent"] == "unknown"
        assert out["fallback_triggered"] is True
        assert out["errors"][0]["node"] == "intent"


# ── Tool Node (_bind_args, on_error 정책, retry) ────────────────────────────


class TestToolNode:
    async def test_happy_path_result_in_slots(self) -> None:
        async def echo_tool(**kwargs):
            return {"echoed": kwargs}

        node = make_tool_node(
            ToolNodeConfig(
                tool_name="echo",
                args_template={"who": "{slot.user}", "input": "{user_input}"},
                timeout_s=1.0,
            ),
            lambda name: echo_tool,
        )
        state = empty_state("t", "s", "sc")
        state["slots"] = {"user": "Alice"}
        state["user_input"] = "hello"
        out = await node(state)
        assert out["slots"]["tool_result"]["echoed"] == {"who": "Alice", "input": "hello"}
        assert out["tool_calls"][0]["tool"] == "echo"

    async def test_timeout_triggers_fallback(self) -> None:
        import asyncio

        async def slow_tool(**kwargs):
            await asyncio.sleep(0.5)

        node = make_tool_node(
            ToolNodeConfig(tool_name="slow", timeout_s=0.05, retry=0),
            lambda name: slow_tool,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["fallback_triggered"] is True
        assert out["tool_calls"][0]["error"].startswith("Tool 'slow' timed out")

    async def test_retry_succeeds_on_second_attempt(self) -> None:
        counter = {"n": 0}

        async def flaky_tool(**kwargs):
            counter["n"] += 1
            if counter["n"] < 2:
                raise RuntimeError("transient")
            return {"ok": True}

        node = make_tool_node(
            ToolNodeConfig(tool_name="flaky", timeout_s=1.0, retry=2),
            lambda name: flaky_tool,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["slots"]["tool_result"] == {"ok": True}
        assert counter["n"] == 2

    async def test_on_error_raise_propagates(self) -> None:
        async def bad_tool(**kwargs):
            raise RuntimeError("boom")

        node = make_tool_node(
            ToolNodeConfig(
                tool_name="bad", timeout_s=1.0, retry=0, on_error="raise"
            ),
            lambda name: bad_tool,
        )
        with pytest.raises(RuntimeError, match="Tool failure"):
            await node(empty_state("t", "s", "sc"))

    async def test_on_error_continue_logs_without_fallback(self) -> None:
        async def bad_tool(**kwargs):
            raise RuntimeError("boom")

        node = make_tool_node(
            ToolNodeConfig(
                tool_name="bad", timeout_s=1.0, retry=0, on_error="continue"
            ),
            lambda name: bad_tool,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out.get("fallback_triggered") is not True
        assert out["errors"][0]["reason"] == "boom"

    async def test_tool_not_registered_triggers_fallback(self) -> None:
        def missing(_name):
            raise KeyError("not registered")

        node = make_tool_node(
            ToolNodeConfig(tool_name="ghost"),
            missing,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["fallback_triggered"] is True


# ── LLM Node (CircuitBreaker OPEN → polite wait) ────────────────────────────


class TestLLMNode:
    async def test_non_streaming_happy_path(self) -> None:
        result = SimpleNamespace(full_text="안녕하세요!", filler_triggered=False)
        service = SimpleNamespace(complete=AsyncMock(return_value=result))

        node = make_llm_node(
            LLMNodeConfig(system_prompt="kind", streaming=False),
            lambda: service,
            stream_sink=None,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["assistant_output"] == "안녕하세요!"
        assert out["messages"][0]["content"] == "안녕하세요!"
        assert "turn_latency_ms" in out

    async def test_circuit_breaker_open_yields_polite_wait(self) -> None:
        from app.domain.circuit_breaker import CircuitBreakerOpenError

        service = SimpleNamespace(
            complete=AsyncMock(side_effect=CircuitBreakerOpenError("open"))
        )
        node = make_llm_node(
            LLMNodeConfig(system_prompt="k", streaming=False),
            lambda: service,
            stream_sink=None,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["fallback_triggered"] is True
        assert "잠시" in out["assistant_output"]
        assert out["errors"][0]["reason"] == "CircuitBreakerOpen"

    async def test_arbitrary_exception_yields_graceful_fallback(self) -> None:
        service = SimpleNamespace(
            complete=AsyncMock(side_effect=RuntimeError("boom"))
        )
        node = make_llm_node(
            LLMNodeConfig(system_prompt="k", streaming=False),
            lambda: service,
            stream_sink=None,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["fallback_triggered"] is True
        assert out["errors"][0]["node"] == "llm"


# ── Transfer Node ───────────────────────────────────────────────────────────


class TestTransferNode:
    async def test_no_client_logs_and_succeeds(self) -> None:
        node = make_transfer_node(
            TransferNodeConfig(queue="vip", reason="user_request"),
            transfer_client_factory=None,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["should_transfer"] is True
        assert out["should_end"] is True
        assert "상담원" in out["assistant_output"]

    async def test_client_failure_still_ends_gracefully(self) -> None:
        client = SimpleNamespace(
            request_transfer=AsyncMock(side_effect=RuntimeError("gRPC down"))
        )
        node = make_transfer_node(
            TransferNodeConfig(queue="q1"),
            transfer_client_factory=lambda: client,
        )
        out = await node(empty_state("t", "s", "sc"))
        assert out["should_end"] is True
        assert out["errors"][0]["node"] == "transfer"
        # 상담원 전환이 실패해도 hangup 안내문
        assert "죄송" in out["assistant_output"]

    async def test_client_success_invokes_request_transfer(self) -> None:
        client = SimpleNamespace(request_transfer=AsyncMock())
        node = make_transfer_node(
            TransferNodeConfig(queue="q1", reason="r1", include_summary=True),
            transfer_client_factory=lambda: client,
        )
        state = empty_state("t", "sess_7", "sc")
        state["intent"] = {"intent": "billing", "confidence": 0.8, "slots": {}}
        out = await node(state)
        client.request_transfer.assert_awaited_once()
        call_kwargs = client.request_transfer.await_args.kwargs
        assert call_kwargs["queue"] == "q1"
        assert call_kwargs["session_id"] == "sess_7"
        assert "billing" in call_kwargs["summary"]
        assert out["should_transfer"] is True
