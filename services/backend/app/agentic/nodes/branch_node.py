"""
Branch Node — 조건부 분기.

LangGraph 에서 조건부 엣지는 함수가 다음 노드 id 문자열을 반환하는 방식이다.
우리는 Branch "노드"를 passthrough로 두고, 컴파일러가 이 노드에서 나가는
엣지 집합을 보고 conditional_edge로 변환한다. 따라서 이 파일은:
  1) passthrough 노드 팩토리
  2) 엣지 디스패처(state -> next_node_id) 팩토리
를 함께 제공한다.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Awaitable, Callable

from app.agentic.scenario_dsl import BranchNode, BranchNodeConfig, Edge
from app.agentic.state import CallbotState


def make_branch_node(config: BranchNodeConfig) -> Callable[[CallbotState], Awaitable[dict]]:
    """Branch 는 상태 변경 없음 — 디스패처가 분기 결정"""

    async def branch_passthrough(state: CallbotState) -> dict:
        return {}

    return branch_passthrough


def make_branch_dispatcher(
    node: BranchNode,
    outgoing_edges: list[Edge],
) -> Callable[[CallbotState], str]:
    """
    LangGraph add_conditional_edges() 에 넘길 라우팅 함수 생성.
    """
    config = node.config
    default_target: str | None = None
    cases: list[tuple[str, str]] = []  # (when_value, target_node_id)

    for e in outgoing_edges:
        if e.when is None or e.when == "default":
            default_target = e.to
        else:
            cases.append((e.when, e.to))

    # fallback_triggered 시에는 default_target으로 강제 폴백
    def dispatch(state: CallbotState) -> str:
        if state.get("fallback_triggered") and default_target:
            return default_target

        mode = config.mode
        value: Any = None
        if mode == "intent":
            value = (state.get("intent") or {}).get("intent")
        elif mode == "slot":
            key = config.slot_key
            value = (state.get("slots") or {}).get(key) if key else None
        elif mode == "expr":
            # expr 모드는 간단 포함/동등 매칭만 (보안상 eval 금지)
            value = state.get("user_input", "")

        for when_val, target in cases:
            if _match(mode, value, when_val):
                return target

        if default_target:
            return default_target
        # 모든 것이 매치 실패 — 그래프를 안전하게 종료
        logging.warning("branch: no match, no default — ending")
        return "__end__"

    return dispatch


def _match(mode: str, value: Any, when: str) -> bool:
    if mode in ("intent", "slot"):
        return str(value) == when
    if mode == "expr":
        # 'contains:<word>' 또는 'regex:<pattern>' 지원, 아니면 exact
        if when.startswith("contains:"):
            return when[len("contains:"):].lower() in str(value).lower()
        if when.startswith("regex:"):
            try:
                return bool(re.search(when[len("regex:"):], str(value)))
            except re.error:
                return False
        return str(value) == when
    return False
