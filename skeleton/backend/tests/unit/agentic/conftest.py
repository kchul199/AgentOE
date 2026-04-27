"""Agentic 모듈 테스트용 공용 픽스처.

CLAUDE.md 원칙:
  - 모든 I/O 비동기 — 픽스처도 async 로 노출해 실제 호출 패턴을 재현
  - Latency is King — 테스트는 외부 I/O 없이 in-memory 로 완결
  - Graceful Fallback — services 의 실패 케이스도 픽스처에서 재현 가능해야 함
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agentic.scenario_compiler import ServiceBundle
from app.agentic.scenario_dsl import (
    BranchNode,
    BranchNodeConfig,
    ContextUpdateNode,
    ContextUpdateNodeConfig,
    Edge,
    EndNode,
    EndNodeConfig,
    IntentNode,
    IntentNodeConfig,
    LLMNode,
    LLMNodeConfig,
    Scenario,
    ToolNode,
    ToolNodeConfig,
    TransferNode,
    TransferNodeConfig,
    WaitNode,
    WaitNodeConfig,
)


@pytest.fixture
def mock_llm_service() -> AsyncMock:
    """LLMService 스텁 — generate/stream 모두 async."""
    svc = AsyncMock()
    svc.generate = AsyncMock(return_value="stubbed reply")
    svc.stream = AsyncMock()
    return svc


@pytest.fixture
def mock_intent_client() -> AsyncMock:
    """인텐트 분류 스텁 — 기본은 'unknown' 반환."""
    client = AsyncMock()
    client.classify = AsyncMock(return_value={"label": "unknown", "score": 0.1})
    return client


@pytest.fixture
def mock_tool_registry() -> MagicMock:
    """Tool 레지스트리 스텁 — 모든 tool_name 은 echo(동일 입력 반환)."""
    async def _echo(**kwargs: Any) -> dict[str, Any]:
        return {"ok": True, "echoed": kwargs}

    registry = MagicMock()
    registry.return_value = _echo  # callable(tool_name) -> tool_fn
    return registry


@pytest.fixture
def mock_transfer_client() -> AsyncMock:
    client = AsyncMock()
    client.transfer = AsyncMock(return_value={"status": "handed_off"})
    return client


@pytest.fixture
def stream_sink_spy() -> AsyncMock:
    """WS sink spy — 노드가 푸시한 이벤트를 call_args_list 로 검증."""
    return AsyncMock()


@pytest.fixture
def service_bundle(
    mock_llm_service: AsyncMock,
    mock_intent_client: AsyncMock,
    mock_tool_registry: MagicMock,
    mock_transfer_client: AsyncMock,
    stream_sink_spy: AsyncMock,
) -> ServiceBundle:
    return ServiceBundle(
        llm_service_factory=lambda: mock_llm_service,
        intent_client_factory=lambda: mock_intent_client,
        tool_registry_getter=mock_tool_registry,
        transfer_client_factory=lambda: mock_transfer_client,
        stream_sink=stream_sink_spy,
    )


# ── 시나리오 팩토리 ─────────────────────────────────────────────────────────


def minimal_valid_scenario() -> Scenario:
    """최소 유효 시나리오: greeting → end."""
    return Scenario(
        scenario_id="test_min",
        tenant_id="t_test",
        name="Minimal",
        entry="greet",
        nodes=[
            LLMNode(
                id="greet",
                config=LLMNodeConfig(system_prompt="hi"),
            ),
            EndNode(id="bye", config=EndNodeConfig(closing_message="bye")),
        ],
        edges=[Edge.model_validate({"from": "greet", "to": "bye"})],
    )


def branch_scenario() -> Scenario:
    """intent mode branch: classify → router → [balance|fallback]."""
    return Scenario(
        scenario_id="test_branch",
        tenant_id="t_test",
        name="Branch",
        entry="classify",
        fallback_node="fallback",
        nodes=[
            IntentNode(
                id="classify",
                config=IntentNodeConfig(labels=["balance", "billing"], threshold=0.3),
            ),
            BranchNode(id="router", config=BranchNodeConfig(mode="intent")),
            LLMNode(id="balance", config=LLMNodeConfig(system_prompt="잔액 안내")),
            LLMNode(id="fallback", config=LLMNodeConfig(system_prompt="다시 말씀해 주세요")),
            EndNode(id="done", config=EndNodeConfig()),
        ],
        edges=[
            Edge.model_validate({"from": "classify", "to": "router"}),
            Edge.model_validate({"from": "router", "when": "balance", "to": "balance"}),
            Edge.model_validate({"from": "router", "when": "default", "to": "fallback"}),
            Edge.model_validate({"from": "balance", "to": "done"}),
            Edge.model_validate({"from": "fallback", "to": "done"}),
        ],
    )


def full_scenario_with_all_node_types() -> Scenario:
    """8개 노드 타입 모두 포함 — 컴파일러 분기 전체 커버."""
    return Scenario(
        scenario_id="test_full",
        tenant_id="t_test",
        name="FullCoverage",
        entry="greet",
        fallback_node="fb",
        nodes=[
            LLMNode(id="greet", config=LLMNodeConfig(system_prompt="hi")),
            WaitNode(id="wait", config=WaitNodeConfig(timeout_s=5)),
            IntentNode(
                id="intent",
                config=IntentNodeConfig(labels=["a", "b"]),
            ),
            BranchNode(id="branch", config=BranchNodeConfig(mode="intent")),
            ToolNode(id="tool", config=ToolNodeConfig(tool_name="echo")),
            ContextUpdateNode(
                id="ctx",
                config=ContextUpdateNodeConfig(set_slots={"k": "v"}),
            ),
            TransferNode(id="xfer", config=TransferNodeConfig(queue="q1")),
            EndNode(id="fb", config=EndNodeConfig(closing_message="fallback")),
            EndNode(id="done", config=EndNodeConfig()),
        ],
        edges=[
            Edge.model_validate({"from": "greet", "to": "wait"}),
            Edge.model_validate({"from": "wait", "to": "intent"}),
            Edge.model_validate({"from": "intent", "to": "branch"}),
            Edge.model_validate({"from": "branch", "when": "a", "to": "tool"}),
            Edge.model_validate({"from": "branch", "when": "b", "to": "xfer"}),
            Edge.model_validate({"from": "branch", "when": "default", "to": "fb"}),
            Edge.model_validate({"from": "tool", "to": "ctx"}),
            Edge.model_validate({"from": "ctx", "to": "done"}),
        ],
    )
