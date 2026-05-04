"""
Transfer Node — 상담원 전환.

흐름:
  1) 현재까지의 대화 요약 생성 (옵션)
  2) VBGW 에 transfer 요청 (gRPC 또는 내부 이벤트)
  3) state["should_transfer"]=True, should_end=True 설정 → 그래프 종료
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

import structlog

from app.agentic.scenario_dsl import TransferNodeConfig
from app.agentic.state import CallbotState, Message

log = structlog.get_logger(__name__)


def make_transfer_node(
    config: TransferNodeConfig,
    transfer_client_factory: Callable[[], Any] | None = None,
) -> Callable[[CallbotState], Awaitable[dict]]:
    """
    transfer_client_factory: VBGW gRPC 클라이언트 팩토리.
    None 이면 no-op (PoC에서 event bus 로그만).
    """

    async def transfer_node(state: CallbotState) -> dict:
        sess = state.get("session", {})
        session_id = sess.get("session_id", "")
        tenant_id = sess.get("tenant_id", "")

        summary = _build_summary(state) if config.include_summary else ""
        start = time.monotonic()

        try:
            if transfer_client_factory is not None:
                client = transfer_client_factory()
                await client.request_transfer(
                    session_id=session_id,
                    tenant_id=tenant_id,
                    queue=config.queue,
                    reason=config.reason,
                    summary=summary,
                )
            else:
                log.info(
                    "transfer.requested (no client)",
                    session_id=session_id,
                    queue=config.queue,
                    reason=config.reason,
                )
        except Exception as exc:
            logging.exception("transfer_node failed")
            # 전환 자체가 실패해도 봇은 종료 — 안내 메시지 후 hangup
            return {
                "assistant_output": "죄송합니다. 잠시 후 다시 시도해 주세요.",
                "messages": [Message(
                    role="assistant",
                    content="죄송합니다. 잠시 후 다시 시도해 주세요.",
                    node_id="transfer-failed",
                )],
                "should_end": True,
                "errors": [{"node": "transfer", "reason": str(exc)[:200]}],
            }

        elapsed_ms = (time.monotonic() - start) * 1000
        log.info("transfer.ok", latency_ms=round(elapsed_ms, 1))
        goodbye = "상담원에게 연결해 드리겠습니다. 잠시만 기다려 주세요."
        return {
            "assistant_output": goodbye,
            "messages": [Message(role="assistant", content=goodbye, node_id="transfer")],
            "should_transfer": True,
            "should_end": True,
        }

    return transfer_node


def _build_summary(state: CallbotState) -> str:
    msgs = state.get("messages", [])[-10:]
    slots = state.get("slots", {})
    intent = (state.get("intent") or {}).get("intent", "unknown")
    lines = [f"[Intent] {intent}", f"[Slots] {slots}", "[Last turns]"]
    for m in msgs:
        role = m.get("role", "?")
        content = (m.get("content", "") or "")[:120]
        lines.append(f"  - {role}: {content}")
    return "\n".join(lines)
