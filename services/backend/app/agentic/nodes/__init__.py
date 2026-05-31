"""
Node implementations for LangGraph scenarios.

각 모듈은 다음을 제공:
    make_<type>_node(config, services) -> async callable (state -> state update)

규칙:
    * 항상 async
    * 실패 시 우아한 Fallback (state["fallback_triggered"]=True, errors 리스트에 append)
    * 절대 raise 로 그래프를 깨뜨리지 않음 (Transfer 엣지로 빠져나가게 함)
    * PII 마스킹은 logging.py가 자동 적용 (로그 상에서). 상태 자체는 원문 유지 (LLM에 필요)
"""

from app.agentic.nodes.branch_node import make_branch_node as make_branch_node
from app.agentic.nodes.context_node import make_context_node as make_context_node
from app.agentic.nodes.end_node import make_end_node as make_end_node
from app.agentic.nodes.intent_node import make_intent_node as make_intent_node
from app.agentic.nodes.llm_node import make_llm_node as make_llm_node
from app.agentic.nodes.tool_node import make_tool_node as make_tool_node
from app.agentic.nodes.transfer_node import make_transfer_node as make_transfer_node
from app.agentic.nodes.wait_node import make_wait_node as make_wait_node

__all__ = [
    "make_branch_node",
    "make_context_node",
    "make_end_node",
    "make_intent_node",
    "make_llm_node",
    "make_tool_node",
    "make_transfer_node",
    "make_wait_node",
]
