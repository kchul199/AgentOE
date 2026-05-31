"""
Callbot Graph Runtime — compile / run / resume / cache

책임:
  1) 테넌트/시나리오 버전별 CompiledGraph 캐시 (LRU)
  2) STT 결과가 도착하면 stream() 호출 → 노드들 순차 실행
  3) Wait 노드 도달 시 interrupt, 다음 턴 입력이 오면 resume
  4) 세션 복구 (Redis Checkpointer)

사용 예:
    graph = await CallbotGraphRuntime.get_for(tenant_id, scenario_id)
    async for event in graph.stream_turn(session_id, user_input, config):
        # event = {"type": "delta"|"filler"|"tool_call"|"final", "data": ...}
        await ws.send_json(event)
"""

from __future__ import annotations

import logging
from collections import OrderedDict
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import structlog

from app.agentic.scenario_compiler import (
    CompiledScenario,
    ServiceBundle,
    compile_scenario,
)
from app.agentic.scenario_dsl import Scenario
from app.agentic.state import CallbotState, empty_state

log = structlog.get_logger(__name__)


# ── 캐시 ──────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class _CacheKey:
    tenant_id: str
    scenario_id: str
    version: int


class _LRUCache:
    """초경량 LRU — 테넌트×시나리오 컴파일 결과 캐싱"""

    def __init__(self, capacity: int = 256) -> None:
        self._cap = capacity
        self._d: OrderedDict[_CacheKey, CompiledScenario] = OrderedDict()

    def get(self, k: _CacheKey) -> CompiledScenario | None:
        if k not in self._d:
            return None
        self._d.move_to_end(k)
        return self._d[k]

    def put(self, k: _CacheKey, v: CompiledScenario) -> None:
        self._d[k] = v
        self._d.move_to_end(k)
        while len(self._d) > self._cap:
            self._d.popitem(last=False)

    def invalidate(self, tenant_id: str, scenario_id: str) -> int:
        """특정 시나리오의 모든 버전 캐시 무효화 (재게시 시 호출)"""
        keys = [k for k in self._d if k.tenant_id == tenant_id and k.scenario_id == scenario_id]
        for k in keys:
            del self._d[k]
        return len(keys)


# ── 런타임 ────────────────────────────────────────────────────────────────────


class CallbotGraphRuntime:
    """
    컴파일된 그래프를 테넌트/세션 경계로 실행.
    싱글톤으로 관리하고, FastAPI 시작시 services 주입.
    """

    _instance: CallbotGraphRuntime | None = None

    def __init__(
        self,
        services: ServiceBundle,
        scenario_loader: Any,  # ScenarioRepository (app.repositories.scenario_repo)
        checkpointer: Any | None = None,
        cache_size: int = 256,
    ) -> None:
        self._services = services
        self._loader = scenario_loader
        self._checkpointer = checkpointer
        self._cache = _LRUCache(capacity=cache_size)

    @classmethod
    def init(
        cls, services: ServiceBundle, scenario_loader: Any, checkpointer: Any | None = None
    ) -> CallbotGraphRuntime:
        cls._instance = cls(services, scenario_loader, checkpointer)
        return cls._instance

    @classmethod
    def instance(cls) -> CallbotGraphRuntime:
        if cls._instance is None:
            raise RuntimeError("CallbotGraphRuntime not initialized — call .init() during startup")
        return cls._instance

    # ── 컴파일 ────────────────────────────────────────────────────────────

    async def _get_compiled(
        self, tenant_id: str, scenario_id: str, version: int | None
    ) -> CompiledScenario:
        scenario: Scenario = await self._loader.load(
            tenant_id=tenant_id,
            scenario_id=scenario_id,
            version=version,
        )
        key = _CacheKey(tenant_id=tenant_id, scenario_id=scenario_id, version=scenario.version)
        hit = self._cache.get(key)
        if hit is not None:
            return hit

        log.info(
            "agentic.compile",
            tenant_id=tenant_id,
            scenario_id=scenario_id,
            version=scenario.version,
        )
        compiled = compile_scenario(scenario, self._services)
        # 실제 langgraph compile (checkpointer + interrupt_before)
        if compiled.graph is not None and not compiled.dry_run:
            compiled = _finalize_compile(compiled, self._checkpointer)

        self._cache.put(key, compiled)
        return compiled

    def invalidate(self, tenant_id: str, scenario_id: str) -> int:
        """시나리오 빌더에서 publish 이후 호출"""
        return self._cache.invalidate(tenant_id, scenario_id)

    # ── 실행 ─────────────────────────────────────────────────────────────

    async def stream_turn(
        self,
        *,
        tenant_id: str,
        session_id: str,
        scenario_id: str,
        user_input: str,
        scenario_version: int | None = None,
    ) -> AsyncIterator[dict]:
        """
        한 턴을 스트리밍 실행. yield 되는 이벤트:
          {"type": "delta",   "text": "..."}
          {"type": "filler",  "text": "..."}
          {"type": "tool",    "tool": "...", "status": "ok"|"error"}
          {"type": "final",   "state": {...}, "transfer": bool}
          {"type": "error",   "reason": "..."}
        """
        compiled = await self._get_compiled(tenant_id, scenario_id, scenario_version)

        if compiled.dry_run or compiled.graph is None:
            yield {"type": "error", "reason": "langgraph unavailable (dry-run)"}
            return

        try:
            runnable = compiled.graph  # CompiledGraph
            config = {
                "configurable": {
                    "thread_id": f"{tenant_id}:{session_id}",
                },
                "recursion_limit": 50,
            }

            # 첫 턴이면 empty_state 로 시드, 이후는 Checkpointer가 복구
            seed: CallbotState | None = None
            try:
                # langgraph 이 get_state를 지원
                existing = runnable.get_state(config)
                if not existing or not existing.values:
                    seed = empty_state(tenant_id, session_id, scenario_id)
            except Exception:
                seed = empty_state(tenant_id, session_id, scenario_id)

            # 다음 입력 반영: user_input 을 state 에 흘려보냄
            input_payload: Any
            if seed is not None:
                seed["user_input"] = user_input
                seed["messages"] = [{"role": "user", "content": user_input, "node_id": "input"}]
                input_payload = seed
            else:
                input_payload = {
                    "user_input": user_input,
                    "messages": [{"role": "user", "content": user_input, "node_id": "input"}],
                }

            async for event in runnable.astream(
                input_payload, config=config, stream_mode="updates"
            ):
                # event = {"<node_id>": <partial_state_update>}
                for node_id, update in event.items():
                    if not isinstance(update, dict):
                        continue
                    if update.get("assistant_output"):
                        # 스트리밍 청크는 llm_node 가 stream_sink 로 이미 푸시했음.
                        # 여기서는 "노드 완료" 이벤트만 방출.
                        yield {"type": "node_done", "node": node_id}
                    if update.get("fallback_triggered"):
                        yield {"type": "fallback", "node": node_id}
                    for tc in update.get("tool_calls", []) or []:
                        yield {
                            "type": "tool",
                            "tool": tc.get("tool"),
                            "status": "error" if tc.get("error") else "ok",
                        }
                    for err in update.get("errors", []) or []:
                        yield {
                            "type": "error_event",
                            "node": err.get("node"),
                            "reason": err.get("reason"),
                        }

            # 최종 state 회수
            final = runnable.get_state(config).values if True else {}
            yield {
                "type": "final",
                "transfer": bool(final.get("should_transfer")),
                "end": bool(final.get("should_end")),
                "assistant_output": final.get("assistant_output", ""),
            }

        except Exception as exc:
            logging.exception("stream_turn failed")
            yield {"type": "error", "reason": str(exc)[:200]}


def _finalize_compile(compiled: CompiledScenario, checkpointer: Any | None) -> CompiledScenario:
    """StateGraph 빌더를 실제 CompiledGraph 로 변환"""
    builder = compiled.graph
    kwargs: dict[str, Any] = {}
    if checkpointer is not None:
        kwargs["checkpointer"] = checkpointer
    if compiled.wait_node_ids:
        kwargs["interrupt_before"] = compiled.wait_node_ids
    graph = builder.compile(**kwargs)  # type: ignore[union-attr]
    return CompiledScenario(
        scenario=compiled.scenario,
        graph=graph,
        node_count=compiled.node_count,
        edge_count=compiled.edge_count,
        wait_node_ids=compiled.wait_node_ids,
        dry_run=False,
    )
