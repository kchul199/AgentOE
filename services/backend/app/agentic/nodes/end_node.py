"""
End Node — 세션 정상 종료. closing_message 가 있으면 TTS 로 송출.
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.agentic.scenario_dsl import EndNodeConfig
from app.agentic.state import CallbotState, Message


def make_end_node(config: EndNodeConfig) -> Callable[[CallbotState], Awaitable[dict]]:
    async def end_node(state: CallbotState) -> dict:
        out: dict = {"should_end": True}
        if config.closing_message:
            out["assistant_output"] = config.closing_message
            out["messages"] = [Message(
                role="assistant",
                content=config.closing_message,
                node_id="end",
            )]
        return out
    return end_node
