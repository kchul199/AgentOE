"""
Wait Node — 사용자 입력 대기.

LangGraph 에서는 Checkpointer 를 이용해 "interrupt" 를 구현한다.
Wait 노드는 실제로는 그래프를 일시 정지 상태로 만드는 신호 노드다.

실제 구현:
    * graph.interrupt_before=[wait_node_id] 로 컴파일 시 등록
    * 런타임에서 사용자의 다음 발화가 도착하면 graph.astream(None, config) 로 resume
    * 본 파일은 메타데이터만 남기는 노드로 둔다 (actual interrupt 는 callbot_graph.py 에서)
"""
from __future__ import annotations

from typing import Awaitable, Callable

from app.agentic.scenario_dsl import WaitNodeConfig
from app.agentic.state import CallbotState


def make_wait_node(config: WaitNodeConfig) -> Callable[[CallbotState], Awaitable[dict]]:
    async def wait_node(state: CallbotState) -> dict:
        # state 에 다음 턴 입력이 올 때까지 대기 표식만 남긴다.
        # LangGraph는 여기서 컴파일 타임의 interrupt_before 설정에 따라 정지한다.
        return {"next_node": None}
    return wait_node
