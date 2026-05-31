"""Scenario DSL Pydantic 유효성 테스트.

커버:
  - Tagged Union 디스크리미네이터 (type 필드)
  - Edge.from 별칭 파싱
  - @model_validator(after): 중복 id / 미존재 entry·fallback·edge 참조 / 도달 불가
  - ScenarioLimits 범위 제약
  - extra=forbid 검증
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.agentic.scenario_dsl import (
    BranchNode,
    BranchNodeConfig,
    Edge,
    EndNode,
    EndNodeConfig,
    IntentNode,
    IntentNodeConfig,
    LLMNode,
    LLMNodeConfig,
    Scenario,
    ScenarioLimits,
)
from tests.unit.agentic.conftest import (
    branch_scenario,
    full_scenario_with_all_node_types,
    minimal_valid_scenario,
)


class TestNodeParsing:
    def test_llm_node_requires_system_prompt(self) -> None:
        with pytest.raises(ValidationError):
            LLMNode(id="n1", config=LLMNodeConfig())  # type: ignore[call-arg]

    def test_intent_node_labels_min_2(self) -> None:
        with pytest.raises(ValidationError, match="at least 2"):
            IntentNode(id="n1", config=IntentNodeConfig(labels=["only_one"]))

    def test_node_id_pattern_rejects_special_chars(self) -> None:
        with pytest.raises(ValidationError):
            EndNode(id="bad id!", config=EndNodeConfig())

    def test_node_id_accepts_hyphen_underscore_alnum(self) -> None:
        n = EndNode(id="node_1-ok", config=EndNodeConfig())
        assert n.id == "node_1-ok"


class TestEdgeAlias:
    def test_edge_from_alias_parses(self) -> None:
        e = Edge.model_validate({"from": "a", "to": "b"})
        assert e.from_ == "a"
        assert e.to == "b"

    def test_edge_supports_both_field_name_and_alias(self) -> None:
        e = Edge(from_="x", to="y")  # populate_by_name=True
        assert e.from_ == "x"


class TestScenarioValidator:
    def test_minimal_valid_scenario(self) -> None:
        s = minimal_valid_scenario()
        assert s.entry == "greet"
        assert len(s.nodes) == 2

    def test_duplicate_node_ids_rejected(self) -> None:
        with pytest.raises(ValidationError, match="duplicate node ids"):
            Scenario(
                scenario_id="dup",
                tenant_id="t",
                name="dup",
                entry="a",
                nodes=[
                    EndNode(id="a", config=EndNodeConfig()),
                    EndNode(id="a", config=EndNodeConfig()),
                ],
                edges=[],
            )

    def test_entry_must_exist_in_nodes(self) -> None:
        with pytest.raises(ValidationError, match="entry node 'missing'"):
            Scenario(
                scenario_id="x",
                tenant_id="t",
                name="x",
                entry="missing",
                nodes=[EndNode(id="a", config=EndNodeConfig())],
                edges=[],
            )

    def test_fallback_node_must_exist(self) -> None:
        with pytest.raises(ValidationError, match="fallback_node 'missing'"):
            Scenario(
                scenario_id="x",
                tenant_id="t",
                name="x",
                entry="a",
                fallback_node="missing",
                nodes=[EndNode(id="a", config=EndNodeConfig())],
                edges=[],
            )

    def test_edge_from_must_reference_node(self) -> None:
        with pytest.raises(ValidationError, match="edge from 'ghost'"):
            Scenario(
                scenario_id="x",
                tenant_id="t",
                name="x",
                entry="a",
                nodes=[EndNode(id="a", config=EndNodeConfig())],
                edges=[Edge.model_validate({"from": "ghost", "to": "a"})],
            )

    def test_edge_to_must_reference_node(self) -> None:
        with pytest.raises(ValidationError, match="edge to 'ghost'"):
            Scenario(
                scenario_id="x",
                tenant_id="t",
                name="x",
                entry="a",
                nodes=[
                    EndNode(id="a", config=EndNodeConfig()),
                    EndNode(id="b", config=EndNodeConfig()),
                ],
                edges=[Edge.model_validate({"from": "a", "to": "ghost"})],
            )

    def test_unreachable_nodes_rejected(self) -> None:
        """'c' 는 entry 에서 도달 불가 → ValidationError."""
        with pytest.raises(ValidationError, match="Unreachable nodes"):
            Scenario(
                scenario_id="x",
                tenant_id="t",
                name="x",
                entry="a",
                nodes=[
                    EndNode(id="a", config=EndNodeConfig()),
                    EndNode(id="b", config=EndNodeConfig()),
                    EndNode(id="c", config=EndNodeConfig()),  # 고아
                ],
                edges=[Edge.model_validate({"from": "a", "to": "b"})],
            )

    def test_fallback_node_not_required_to_be_reachable(self) -> None:
        """fallback_node 는 동적 진입이므로 edge 연결 없어도 OK."""
        s = Scenario(
            scenario_id="x",
            tenant_id="t",
            name="x",
            entry="a",
            fallback_node="fb",
            nodes=[
                EndNode(id="a", config=EndNodeConfig()),
                EndNode(id="fb", config=EndNodeConfig()),
            ],
            edges=[],
        )
        assert s.fallback_node == "fb"


class TestScenarioLimits:
    def test_max_turns_upper_bound(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioLimits(max_turns=999)  # > 200

    def test_max_turns_lower_bound(self) -> None:
        with pytest.raises(ValidationError):
            ScenarioLimits(max_turns=0)

    def test_defaults_sane(self) -> None:
        lim = ScenarioLimits()
        assert 1 <= lim.max_turns <= 200
        assert lim.max_cost_cents_per_session >= 0


class TestScenarioExtraForbid:
    def test_unknown_top_level_field_rejected(self) -> None:
        with pytest.raises(ValidationError, match="[Ee]xtra"):
            Scenario.model_validate(
                {
                    "scenario_id": "x",
                    "tenant_id": "t",
                    "name": "x",
                    "entry": "a",
                    "nodes": [{"id": "a", "type": "end", "config": {}}],
                    "edges": [],
                    "rogue_field": "should reject",
                }
            )


class TestBranchScenario:
    def test_branch_scenario_loads(self) -> None:
        s = branch_scenario()
        assert any(isinstance(n, BranchNode) for n in s.nodes)

    def test_full_coverage_scenario_loads(self) -> None:
        s = full_scenario_with_all_node_types()
        types_present = {type(n).__name__ for n in s.nodes}
        assert {
            "LLMNode",
            "WaitNode",
            "IntentNode",
            "BranchNode",
            "ToolNode",
            "ContextUpdateNode",
            "TransferNode",
            "EndNode",
        } <= types_present


class TestTaggedUnion:
    def test_json_roundtrip_preserves_types(self) -> None:
        s = branch_scenario()
        # dict → Scenario 라운드트립
        data = s.model_dump(by_alias=True)
        s2 = Scenario.model_validate(data)
        assert len(s2.nodes) == len(s.nodes)
        # Branch 노드는 discriminator 로 정확히 복원
        assert isinstance(s2.nodes[1], BranchNode)

    def test_branch_node_config_mode_default_intent(self) -> None:
        b = BranchNode(id="b", config=BranchNodeConfig())
        assert b.config.mode == "intent"
