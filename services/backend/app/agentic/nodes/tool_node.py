"""
Tool Node — 외부 도구/커넥터 호출.

* app.connectors.registry 에 등록된 tool_name 을 호출한다.
* args_template 은 {slot} 치환을 거쳐 실제 인자로 바인딩.
* timeout / retry 내장.
* 실패 시 on_error 정책에 따라 분기:
    - "fallback": state["fallback_triggered"]=True (Branch/엣지로 fallback 노드 진입)
    - "raise":    예외 전파 (그래프 종료 → 상담원 전환)
    - "continue": 에러만 기록, 다음 노드 계속
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Awaitable, Callable

import structlog

from app.agentic.scenario_dsl import ToolNodeConfig
from app.agentic.state import CallbotState

log = structlog.get_logger(__name__)


class ToolNotFoundError(Exception):
    pass


def _bind_args(template: dict[str, str], state: CallbotState) -> dict[str, Any]:
    """{slot.foo} / {user_input} 형태의 자리표시자를 실제 값으로 치환."""
    bound: dict[str, Any] = {}
    ctx = {
        "user_input": state.get("user_input", ""),
        "intent": (state.get("intent") or {}).get("intent", ""),
        **{f"slot.{k}": v for k, v in (state.get("slots", {}) or {}).items()},
    }
    for key, val in template.items():
        if isinstance(val, str):
            try:
                bound[key] = val.format(**ctx)
            except Exception:
                bound[key] = val
        else:
            bound[key] = val
    return bound


def make_tool_node(
    config: ToolNodeConfig,
    tool_registry_getter: Callable[[str], Callable[..., Awaitable[Any]]],
) -> Callable[[CallbotState], Awaitable[dict]]:
    """
    tool_registry_getter(tool_name) -> async callable (**kwargs) -> result
    """

    async def tool_node(state: CallbotState) -> dict:
        try:
            tool = tool_registry_getter(config.tool_name)
        except (KeyError, ToolNotFoundError):
            err = f"Tool '{config.tool_name}' not registered"
            return _handle_error(config, err, state, exc=None)

        args = _bind_args(config.args_template, state)
        start = time.monotonic()

        for attempt in range(config.retry + 1):
            try:
                result = await asyncio.wait_for(tool(**args), timeout=config.timeout_s)
                elapsed_ms = (time.monotonic() - start) * 1000
                log.info(
                    "tool_node.ok",
                    tool=config.tool_name,
                    latency_ms=round(elapsed_ms, 1),
                    attempt=attempt + 1,
                )
                # 결과는 slots["tool_result"] 에 넣어 이후 LLM/Branch가 참조
                return {
                    "slots": {**state.get("slots", {}), "tool_result": result},
                    "tool_calls": [{
                        "tool": config.tool_name,
                        "args": args,
                        "result": _truncate(result),
                        "latency_ms": elapsed_ms,
                    }],
                }
            except asyncio.TimeoutError:
                if attempt == config.retry:
                    return _handle_error(
                        config, f"Tool '{config.tool_name}' timed out", state, exc=None
                    )
                log.warning("tool_node.retry", tool=config.tool_name, attempt=attempt + 1)
            except Exception as exc:
                if attempt == config.retry:
                    return _handle_error(config, str(exc)[:200], state, exc=exc)
                log.warning("tool_node.retry", tool=config.tool_name, error=str(exc)[:80])

        return _handle_error(config, "unreachable", state, exc=None)

    return tool_node


def _handle_error(
    config: ToolNodeConfig,
    reason: str,
    state: CallbotState,
    exc: Exception | None,
) -> dict:
    if exc:
        logging.exception("tool_node failed: %s", reason)

    err_entry = {"node": "tool", "tool": config.tool_name, "reason": reason}

    if config.on_error == "raise":
        raise RuntimeError(f"Tool failure (raise policy): {reason}")
    if config.on_error == "continue":
        return {
            "errors": [err_entry],
            "tool_calls": [{"tool": config.tool_name, "error": reason}],
        }
    # default: fallback
    return {
        "fallback_triggered": True,
        "errors": [err_entry],
        "tool_calls": [{"tool": config.tool_name, "error": reason}],
    }


def _truncate(obj: Any, limit: int = 500) -> Any:
    """로그/감사용 결과 압축. 원본은 state.slots 에 그대로 남음."""
    try:
        s = str(obj)
        return s if len(s) <= limit else s[:limit] + "…"
    except Exception:
        return "<unserializable>"
