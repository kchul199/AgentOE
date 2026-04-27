"""
Agentic AI Module — LangGraph-based tenant-scoped callbot scenarios.

구조:
    state.py           — CallbotState (그래프 전체가 공유하는 TypedDict)
    scenario_dsl.py    — Pydantic DSL (JSON/YAML → 내부 IR)
    scenario_compiler.py — DSL → LangGraph StateGraph 컴파일러
    callbot_graph.py   — 런타임 엔트리 (compile / stream / resume)
    router.py          — Strangler Fig: 테넌트별 플래그로 AIPipeline ↔ LangGraph 라우팅
    nodes/             — 노드 구현체 (intent, llm, tool, branch, transfer, wait, context)
    scenarios/         — 샘플 시나리오 JSON

CLAUDE.md 원칙 준수:
    * 모든 노드는 async (비동기 I/O)
    * Tool 호출 실패 시 Fallback 엣지로 우아한 폴백
    * Latency 최소화: Checkpointer는 Redis 기반 (Mongo 대비 ~10x 빠름)
"""
from app.agentic.state import CallbotState  # noqa: F401
from app.agentic.scenario_dsl import Scenario, Node, Edge  # noqa: F401
from app.agentic.scenario_compiler import compile_scenario  # noqa: F401
