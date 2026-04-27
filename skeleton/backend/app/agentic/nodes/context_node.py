"""
Context Update Node — LLM 호출 없이 슬롯만 조작.

예: 인증 성공 후 slot["authenticated"]=True 로 고정하거나,
    분기 전에 slot["account_type"] 를 강제 세팅.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.agentic.scenario_dsl import ContextUpdateNodeConfig
from app.agentic.state import CallbotState


def make_context_node(
    config: ContextUpdateNodeConfig,
) -> Callable[[CallbotState], Awaitable[dict]]:
    async def context_node(state: CallbotState) -> dict:
        slots = dict(state.get("slots", {}))
        slots.update(config.set_slots)
        for k in config.clear_slots:
            slots.pop(k, None)
        return {"slots": slots}
    return context_node
