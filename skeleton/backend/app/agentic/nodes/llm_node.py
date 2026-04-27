"""
LLM Node — 기존 LLMService 를 래핑, 스트리밍 응답 + Filler 지원.

LangGraph 노드는 "완료 상태"를 반환해야 하므로, 스트리밍 청크는
병렬 WebSocket 경로로 흘려보내고 상태에는 누적된 full_text를 저장한다.

Fallback:
    * Primary LLM 실패 → fallback_model 자동 사용 (LLMService 내장)
    * Circuit Breaker OPEN → fallback_triggered=True, assistant_output=<polite wait message>
"""
from __future__ import annotations

import logging
import time
from typing import Any, Awaitable, Callable

import structlog

from app.agentic.scenario_dsl import LLMNodeConfig
from app.agentic.state import CallbotState, Message
from app.core.quota import QuotaExceededError
from app.domain.circuit_breaker import CircuitBreakerOpenError

log = structlog.get_logger(__name__)

_POLITE_WAIT = "죄송합니다. 시스템이 잠시 바빠서요. 다시 말씀해 주시겠어요?"
_QUOTA_WAIT = "죄송합니다. 오늘 처리량이 많아 잠시 후 다시 시도해 주세요."


def _render_prompt(template: str | None, state: CallbotState) -> str:
    """아주 단순한 {var} 치환. 실제 프로덕션에서는 jinja2-lite 권장."""
    if not template:
        return state.get("user_input", "")
    try:
        return template.format(
            user_input=state.get("user_input", ""),
            intent=(state.get("intent") or {}).get("intent", ""),
            slots=state.get("slots", {}),
        )
    except Exception:
        return state.get("user_input", "")


def make_llm_node(
    config: LLMNodeConfig,
    llm_service_factory: Callable[[], Any],
    stream_sink: Callable[[str, dict], Awaitable[None]] | None = None,
) -> Callable[[CallbotState], Awaitable[dict]]:
    """
    llm_service_factory: app.services.llm_service.LLMService 인스턴스 팩토리
    stream_sink:          (session_id, chunk_dict) → None. WS로 청크 푸시용.
                          None이면 non-streaming 축약 (complete 호출)
    """

    async def llm_node(state: CallbotState) -> dict:
        prompt = _render_prompt(config.prompt_template, state)
        sess = state.get("session", {})
        session_id = sess.get("session_id", "")
        tenant_id = sess.get("tenant_id") or None
        history = [
            {"role": m["role"], "content": m["content"]}
            for m in state.get("messages", [])
            if m.get("role") in {"user", "assistant"} and m.get("content")
        ]

        service = llm_service_factory()
        start = time.monotonic()

        try:
            if config.streaming and stream_sink is not None:
                full_text: list[str] = []
                filler_triggered = False
                async for chunk in service.stream(
                    user_text=prompt,
                    history=history,
                    system_prompt=config.system_prompt,
                    tenant_id=tenant_id,
                ):
                    if chunk.is_filler:
                        filler_triggered = True
                        await stream_sink(
                            session_id,
                            {"type": "filler", "text": chunk.text},
                        )
                    elif chunk.is_final:
                        full_text = [chunk.text]
                    else:
                        full_text.append(chunk.text)
                        await stream_sink(
                            session_id,
                            {"type": "delta", "text": chunk.text},
                        )
                text = "".join(full_text)
            else:
                result = await service.complete(
                    user_text=prompt,
                    history=history,
                    system_prompt=config.system_prompt,
                    tenant_id=tenant_id,
                )
                text = result.full_text
                filler_triggered = result.filler_triggered

            elapsed_ms = (time.monotonic() - start) * 1000
            log.info(
                "llm_node.complete",
                latency_ms=round(elapsed_ms, 1),
                filler=filler_triggered,
                chars=len(text),
            )

            new_msg = Message(
                role="assistant",
                content=text,
                node_id="llm",
            )
            return {
                "assistant_output": text,
                "messages": [new_msg],
                "turn_latency_ms": elapsed_ms,
            }

        except CircuitBreakerOpenError:
            logging.warning("llm_node: CB OPEN — returning polite wait")
            return {
                "assistant_output": _POLITE_WAIT,
                "messages": [Message(role="assistant", content=_POLITE_WAIT, node_id="llm-cb-open")],
                "fallback_triggered": True,
                "errors": [{"node": "llm", "reason": "CircuitBreakerOpen"}],
            }
        except QuotaExceededError as qe:
            # graceful=True: fallback 시나리오로 분기 허용 (통화 유지)
            # graceful=False: 즉시 상위로 전파 → 오케스트레이터가 HTTP 429/세션 종료 결정
            if not qe.graceful:
                raise
            log.warning(
                "llm_node.quota_exceeded_graceful",
                scope=qe.scope, tenant_id=tenant_id, session_id=session_id,
            )
            return {
                "assistant_output": _QUOTA_WAIT,
                "messages": [Message(role="assistant", content=_QUOTA_WAIT, node_id="llm-quota")],
                "fallback_triggered": True,
                "errors": [{"node": "llm", "reason": f"QuotaExceeded:{qe.scope}"}],
            }
        except Exception as exc:
            logging.exception("llm_node failed")
            return {
                "assistant_output": _POLITE_WAIT,
                "messages": [Message(role="assistant", content=_POLITE_WAIT, node_id="llm-error")],
                "fallback_triggered": True,
                "errors": [{"node": "llm", "reason": str(exc)[:200]}],
            }

    return llm_node
