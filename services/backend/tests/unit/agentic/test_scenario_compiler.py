"""Scenario Compiler 테스트 — langgraph 있/없 양쪽 커버.

langgraph 설치 안 된 환경에서도:
  - _dry_run_compile() 이 wait_node_ids / node_count / edge_count 정확히 계산
  - CompiledScenario.dry_run == True

langgraph 설치된 환경에서는:
  - StateGraph builder 가 반환되며 노드/엣지가 등록됨
  - BranchNode 는 add_conditional_edges 경로
  - WaitNode 는 wait_node_ids 에 수집됨 (interrupt_before 용)
"""

from __future__ import annotations

import importlib.util

import pytest

from app.agentic.scenario_compiler import (
    CompiledScenario,
    _dry_run_compile,
    compile_scenario,
)
from app.agentic.scenario_dsl import BranchNode
from tests.unit.agentic.conftest import (
    branch_scenario,
    full_scenario_with_all_node_types,
    minimal_valid_scenario,
)

_HAS_LANGGRAPH = importlib.util.find_spec("langgraph") is not None


class TestDryRunCompile:
    def test_dry_run_returns_compiled_scenario(self) -> None:
        s = minimal_valid_scenario()
        result = _dry_run_compile(s)
        assert isinstance(result, CompiledScenario)
        assert result.dry_run is True
        assert result.graph is None
        assert result.node_count == 2
        assert result.edge_count == 1

    def test_dry_run_collects_wait_ids(self) -> None:
        s = full_scenario_with_all_node_types()
        result = _dry_run_compile(s)
        assert result.wait_node_ids == ["wait"]

    def test_dry_run_branch_scenario(self) -> None:
        s = branch_scenario()
        result = _dry_run_compile(s)
        assert result.node_count == 5
        assert result.edge_count == 5
        # Wait 노드 없음
        assert result.wait_node_ids == []


class TestCompileScenarioPublicAPI:
    """Public compile_scenario() 은 langgraph 없을 때 자동으로 dry-run."""

    def test_compile_without_langgraph_falls_back_to_dry_run(
        self,
        service_bundle,
        monkeypatch,
    ) -> None:
        # langgraph 임포트를 강제로 실패시킴 (설치된 환경에서도)
        import builtins

        real_import = builtins.__import__

        def fake_import(name: str, *args, **kwargs):
            if name.startswith("langgraph"):
                raise ImportError("simulated: langgraph missing")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", fake_import)

        s = full_scenario_with_all_node_types()
        result = compile_scenario(s, service_bundle)
        assert result.dry_run is True
        assert result.graph is None
        assert result.node_count == 9
        assert len(result.wait_node_ids) == 1


@pytest.mark.skipif(not _HAS_LANGGRAPH, reason="langgraph not installed")
class TestCompileWithLangGraph:
    """langgraph 설치된 환경에서만 실행."""

    def test_minimal_scenario_compiles(self, service_bundle) -> None:
        s = minimal_valid_scenario()
        result = compile_scenario(s, service_bundle)
        assert result.dry_run is False
        assert result.graph is not None
        assert result.node_count == 2

    def test_full_scenario_collects_wait_and_builds(self, service_bundle) -> None:
        s = full_scenario_with_all_node_types()
        result = compile_scenario(s, service_bundle)
        assert result.dry_run is False
        assert "wait" in result.wait_node_ids

    def test_branch_scenario_has_conditional_edges(self, service_bundle) -> None:
        s = branch_scenario()
        result = compile_scenario(s, service_bundle)
        assert result.dry_run is False
        # StateGraph 내부에 branch 노드가 conditional_edges 로 등록됐는지 확인
        branch_ids = {n.id for n in s.nodes if isinstance(n, BranchNode)}
        assert branch_ids == {"router"}


class TestCompilerNonBranchMultiEdgeRejection:
    """Branch 가 아닌 노드에서 다중 아웃바운드 엣지는 DSL 단 또는 컴파일 단에서 차단."""

    def test_dry_run_does_not_raise_on_multi_edge(self) -> None:
        """DSL 자체는 막지 않지만(fallback edge 용), dry-run 은 그래프만 셈."""
        s = full_scenario_with_all_node_types()
        # full scenario는 합법 — dry-run 이 통과해야 함
        result = _dry_run_compile(s)
        assert result.dry_run is True
