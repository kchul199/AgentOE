"""
Scenario DSL — JSON/YAML로 기술하는 콜봇 시나리오 정의

DSL 구조:
    {
      "scenario_id": "cs_account_v3",
      "tenant_id": "t_acme",
      "version": 3,
      "entry": "greeting",
      "nodes": [
        {"id": "greeting", "type": "llm", "config": {"prompt_template": "..."}},
        {"id": "classify", "type": "intent", "config": {"labels": [...]}},
        ...
      ],
      "edges": [
        {"from": "greeting", "to": "classify"},
        {"from": "classify", "when": "intent == 'billing'", "to": "billing_flow"},
        {"from": "classify", "when": "default", "to": "fallback"}
      ],
      "fallback_node": "fallback_agent",
      "max_turns": 30
    }

설계:
    * Pydantic v2 기반 (strict mode, extra=forbid)
    * GUI Scenario Builder가 이 스키마에 맞춰 JSON 생성
    * scenario_compiler가 이 DSL을 LangGraph StateGraph로 변환
"""
from __future__ import annotations

from typing import Any, Literal, Union

from pydantic import BaseModel, Field, model_validator


# ── 노드 타입 — Tagged Union ─────────────────────────────────────────────────

class _NodeBase(BaseModel):
    id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_\-]+$")
    label: str | None = None
    description: str | None = None


class IntentNodeConfig(BaseModel):
    """인텐트 분류 — 소형 LLM 또는 정규식/키워드 기반"""
    labels: list[str] = Field(..., min_length=2)
    model: str = "groq-llama-3.3-70b"          # 분류 전용 소형 모델 교체 가능
    prompt_template: str | None = None
    threshold: float = 0.5


class IntentNode(_NodeBase):
    type: Literal["intent"] = "intent"
    config: IntentNodeConfig


class LLMNodeConfig(BaseModel):
    """LLM 응답 생성 — 스트리밍 + Filler 전략"""
    model: str = "groq-llama-4-scout"
    fallback_model: str = "groq-llama-3.3-70b"
    system_prompt: str
    prompt_template: str | None = None  # 사용자 입력 렌더링용 (jinja2-lite)
    temperature: float = 0.7
    max_tokens: int = 512
    streaming: bool = True
    enable_filler: bool = True


class LLMNode(_NodeBase):
    type: Literal["llm"] = "llm"
    config: LLMNodeConfig


class ToolNodeConfig(BaseModel):
    """외부 도구/커넥터 호출 (MCP, HTTP, DB 등)"""
    tool_name: str = Field(..., description="registered in app.connectors.registry")
    args_template: dict[str, str] = Field(default_factory=dict)
    timeout_s: float = 5.0
    retry: int = 1
    on_error: Literal["fallback", "raise", "continue"] = "fallback"


class ToolNode(_NodeBase):
    type: Literal["tool"] = "tool"
    config: ToolNodeConfig


class BranchNodeConfig(BaseModel):
    """조건 분기 — 엣지의 when 절로 다음 노드를 선택"""
    # 조건 평가 방식: 'expr' = 간단한 파이썬식, 'intent' = 현재 intent 매칭
    mode: Literal["expr", "intent", "slot"] = "intent"
    # slot 모드일 때 어느 슬롯을 검사할지
    slot_key: str | None = None


class BranchNode(_NodeBase):
    type: Literal["branch"] = "branch"
    config: BranchNodeConfig


class TransferNodeConfig(BaseModel):
    """상담원 전환 (VBGW에 SIP REFER 송출)"""
    queue: str = "default"
    reason: str = "user_request"
    include_summary: bool = True


class TransferNode(_NodeBase):
    type: Literal["transfer"] = "transfer"
    config: TransferNodeConfig


class WaitNodeConfig(BaseModel):
    """사용자 입력 대기 (다음 STT 결과가 올 때까지 그래프 일시 정지)"""
    timeout_s: float = 15.0
    prompt_on_timeout: str | None = "죄송합니다, 말씀 못 들었습니다. 다시 말씀해 주세요."


class WaitNode(_NodeBase):
    type: Literal["wait"] = "wait"
    config: WaitNodeConfig


class ContextUpdateNodeConfig(BaseModel):
    """슬롯/메모리 업데이트 (LLM 호출 없이 상태만 변경)"""
    set_slots: dict[str, Any] = Field(default_factory=dict)
    clear_slots: list[str] = Field(default_factory=list)


class ContextUpdateNode(_NodeBase):
    type: Literal["context"] = "context"
    config: ContextUpdateNodeConfig


class EndNodeConfig(BaseModel):
    closing_message: str | None = None


class EndNode(_NodeBase):
    type: Literal["end"] = "end"
    config: EndNodeConfig = EndNodeConfig()


# Union type for discriminated parsing
Node = Union[
    IntentNode, LLMNode, ToolNode, BranchNode,
    TransferNode, WaitNode, ContextUpdateNode, EndNode,
]


# ── 엣지 ─────────────────────────────────────────────────────────────────────

class Edge(BaseModel):
    from_: str = Field(..., alias="from", min_length=1)
    to: str = Field(..., min_length=1)
    # when 은 BranchNode에서만 의미 있음 (intent 값 또는 'default')
    when: str | None = None
    # 조건 설명 (GUI 렌더링용)
    label: str | None = None

    model_config = {"populate_by_name": True}


# ── 시나리오 ─────────────────────────────────────────────────────────────────

class ScenarioLimits(BaseModel):
    max_turns: int = Field(default=30, ge=1, le=200)
    max_duration_s: int = Field(default=900, ge=10, le=3600)
    max_tool_calls_per_turn: int = Field(default=3, ge=0, le=10)
    max_cost_cents_per_session: float = Field(default=50.0, ge=0)


class Scenario(BaseModel):
    scenario_id: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_\-]+$")
    tenant_id: str = Field(..., min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    name: str
    description: str | None = None

    entry: str = Field(..., description="시작 노드 id")
    fallback_node: str | None = Field(
        default=None,
        description="Tool/LLM 실패 시 진입하는 폴백 노드 id (CLAUDE.md: 우아한 Fallback 필수)",
    )

    nodes: list[Node]
    edges: list[Edge]
    limits: ScenarioLimits = ScenarioLimits()

    # 메타데이터
    tags: list[str] = Field(default_factory=list)
    created_by: str | None = None
    created_at: str | None = None   # ISO 8601
    published: bool = False          # False면 draft

    model_config = {"extra": "forbid"}

    @model_validator(mode="after")
    def _validate_graph(self) -> "Scenario":
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes):
            raise ValueError("Scenario has duplicate node ids")
        if self.entry not in ids:
            raise ValueError(f"entry node '{self.entry}' not in nodes")
        if self.fallback_node and self.fallback_node not in ids:
            raise ValueError(f"fallback_node '{self.fallback_node}' not in nodes")
        for e in self.edges:
            if e.from_ not in ids:
                raise ValueError(f"edge from '{e.from_}' not in nodes")
            if e.to not in ids:
                raise ValueError(f"edge to '{e.to}' not in nodes")
        # entry로부터 도달 가능한지 얕게 체크
        reachable = {self.entry}
        changed = True
        while changed:
            changed = False
            for e in self.edges:
                if e.from_ in reachable and e.to not in reachable:
                    reachable.add(e.to)
                    changed = True
        unreachable = ids - reachable
        # fallback은 edge로 연결 안 될 수 있음 (동적 진입)
        if self.fallback_node:
            unreachable.discard(self.fallback_node)
        if unreachable:
            raise ValueError(f"Unreachable nodes: {sorted(unreachable)}")
        return self
