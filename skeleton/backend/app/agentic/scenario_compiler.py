"""
Scenario Compiler — DSL(Scenario) → LangGraph StateGraph

입력:
    scenario: app.agentic.scenario_dsl.Scenario
    services: ServiceBundle (llm, intent client, tool registry, transfer client, stream sink)

출력:
    compiled_graph: LangGraph CompiledGraph (tenant/scenario 별 캐시 키로 재사용)

LangGraph API 가 설치돼 있을 때만 실제 컴파일 가능.
개발/테스트 환경에서 langgraph 가 없으면 ImportError 대신 DRY_RUN 모드로 구조만 검증한다.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from app.agentic.scenario_dsl import (
    BranchNode,
    ContextUpdateNode,
    Edge,
    EndNode,
    IntentNode,
    LLMNode,
    Scenario,
    ToolNode,
    TransferNode,
    WaitNode,
)
from app.agentic.nodes import (
    make_branch_node,
    make_context_node,
    make_end_node,
    make_intent_node,
    make_llm_node,
    make_tool_node,
    make_transfer_node,
    make_wait_node,
)
from app.agentic.nodes.branch_node import make_branch_dispatcher

log = logging.getLogger(__name__)


@dataclass
class ServiceBundle:
    """컴파일 타임에 노드들에 주입되는 서비스 의존성."""
    llm_service_factory: Callable[[], Any]
    intent_client_factory: Callable[[], Any]
    tool_registry_getter: Callable[[str], Callable[..., Awaitable[Any]]]
    transfer_client_factory: Callable[[], Any] | None = None
    stream_sink: Callable[[str, dict], Awaitable[None]] | None = None


@dataclass
class CompiledScenario:
    """컴파일된 결과. langgraph 미설치 시 graph=None."""
    scenario: Scenario
    graph: Any | None  # CompiledGraph
    node_count: int
    edge_count: int
    wait_node_ids: list[str] = field(default_factory=list)  # interrupt_before 대상
    dry_run: bool = False


def compile_scenario(scenario: Scenario, services: ServiceBundle) -> CompiledScenario:
    """DSL 시나리오를 LangGraph StateGraph 로 컴파일."""
    try:
        from langgraph.graph import END, StateGraph
        from app.agentic.state import CallbotState
    except ImportError:
        log.warning("langgraph not installed — dry-run compile only")
        return _dry_run_compile(scenario)

    builder = StateGraph(CallbotState)

    branch_outgoing: dict[str, list[Edge]] = {}
    for e in scenario.edges:
        branch_outgoing.setdefault(e.from_, []).append(e)

    wait_ids: list[str] = []

    # ── 노드 추가 ──────────────────────────────────────────────────────────
    for node in scenario.nodes:
        if isinstance(node, IntentNode):
            fn = make_intent_node(node.config, services.intent_client_factory)
        elif isinstance(node, LLMNode):
            fn = make_llm_node(
                node.config,
                services.llm_service_factory,
                services.stream_sink,
            )
        elif isinstance(node, ToolNode):
            fn = make_tool_node(node.config, services.tool_registry_getter)
        elif isinstance(node, BranchNode):
            fn = make_branch_node(node.config)
        elif isinstance(node, TransferNode):
            fn = make_transfer_node(node.config, services.transfer_client_factory)
        elif isinstance(node, WaitNode):
            fn = make_wait_node(node.config)
            wait_ids.append(node.id)
        elif isinstance(node, ContextUpdateNode):
            fn = make_context_node(node.config)
        elif isinstance(node, EndNode):
            fn = make_end_node(node.config)
        else:
            raise ValueError(f"Unknown node type: {type(node).__name__}")

        builder.add_node(node.id, fn)

    # ── 엣지 추가 ──────────────────────────────────────────────────────────
    branch_node_ids = {n.id for n in scenario.nodes if isinstance(n, BranchNode)}

    for node in scenario.nodes:
        outgoing = branch_outgoing.get(node.id, [])
        if isinstance(node, BranchNode):
            dispatcher = make_branch_dispatcher(node, outgoing)
            # 매핑 dict: {when_value: target_node}
            mapping = {
                (e.when or "default"): e.to for e in outgoing
            }
            # __end__ 는 END 와 매핑
            if "default" not in mapping and scenario.fallback_node:
                mapping["default"] = scenario.fallback_node
            # add_conditional_edges는 mapping 을 함수 반환값 → 실제 노드 id로 변환
            # dispatcher는 이미 실제 노드 id 를 반환하므로 identity 매핑 사용
            identity_mapping = {v: v for v in mapping.values()}
            identity_mapping["__end__"] = END
            builder.add_conditional_edges(node.id, dispatcher, identity_mapping)
        elif isinstance(node, (TransferNode, EndNode)):
            # 터미널 노드: END 로 연결
            builder.add_edge(node.id, END)
        else:
            # 단일 후속 노드가 있으면 직접 엣지, 여러개면 첫번째만 (DSL 검증으로 방지됨)
            if len(outgoing) == 1:
                builder.add_edge(node.id, outgoing[0].to)
            elif len(outgoing) == 0:
                # leaf — END 로
                builder.add_edge(node.id, END)
            else:
                # Branch가 아닌 노드에서 다중 엣지는 DSL 단에서 금지됨
                raise ValueError(
                    f"Non-branch node '{node.id}' has {len(outgoing)} outgoing edges"
                )

    builder.set_entry_point(scenario.entry)

    # Checkpointer 는 호출자가 compile() 시 주입 (Redis 기반)
    # 여기서는 compile 시점에만 필요한 구성 요소 (interrupt_before) 를 반환
    # 실제 compile 은 callbot_graph.py 에서 compile(checkpointer=..., interrupt_before=wait_ids)
    graph = builder

    return CompiledScenario(
        scenario=scenario,
        graph=graph,
        node_count=len(scenario.nodes),
        edge_count=len(scenario.edges),
        wait_node_ids=wait_ids,
        dry_run=False,
    )


def _dry_run_compile(scenario: Scenario) -> CompiledScenario:
    """langgraph 미설치 환경에서 DSL 유효성만 확인하고 통계 반환."""
    wait_ids = [n.id for n in scenario.nodes if isinstance(n, WaitNode)]
    return CompiledScenario(
        scenario=scenario,
        graph=None,
        node_count=len(scenario.nodes),
        edge_count=len(scenario.edges),
        wait_node_ids=wait_ids,
        dry_run=True,
    )
