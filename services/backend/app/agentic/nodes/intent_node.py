"""
Intent Node — 사용자 입력을 사전 정의된 라벨 중 하나로 분류.

구현:
    * 작은 LLM(Llama 3.3 70B)으로 few-shot 분류 요청
    * Groq 전용: temperature=0, max_tokens=32
    * 실패 시 intent='unknown', confidence=0, fallback_triggered=True
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

import structlog

from app.agentic.scenario_dsl import IntentNodeConfig
from app.agentic.state import CallbotState, IntentResult
from app.core.timeouts import SVC_LLM, with_timeout

log = structlog.get_logger(__name__)

_DEFAULT_PROMPT = """You are a strict intent classifier for a Korean call center bot.
Allowed labels: {labels}
Return ONLY JSON: {{"intent": "<label>", "confidence": <0..1>, "slots": {{}}}}

User said (ko-KR): "{user_input}"
"""


def make_intent_node(
    config: IntentNodeConfig,
    llm_client_factory: Callable[[], Any],
) -> Callable[[CallbotState], Awaitable[dict]]:
    """
    config 를 클로저로 잡아 async 함수를 반환.
    llm_client_factory: LangGraph 노드가 실행될 때 Groq AsyncClient 를 가져오는 콜백.
    """
    labels_str = ", ".join(config.labels)

    async def intent_node(state: CallbotState) -> dict:
        user_input = state.get("user_input", "")
        if not user_input:
            return {
                "intent": IntentResult(intent="unknown", confidence=0.0, slots={}),
                "errors": [{"node": "intent", "reason": "empty user_input"}],
                "fallback_triggered": True,
            }

        prompt = (config.prompt_template or _DEFAULT_PROMPT).format(
            labels=labels_str, user_input=user_input
        )

        start = time.monotonic()
        try:
            client = llm_client_factory()
            response = await with_timeout(
                client.chat.completions.create(
                    model=config.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.0,
                    max_tokens=64,
                    response_format={"type": "json_object"},
                ),
                service=SVC_LLM,
            )
            raw = response.choices[0].message.content or "{}"
            parsed = json.loads(raw)
            intent = parsed.get("intent", "unknown")
            confidence = float(parsed.get("confidence", 0.0))
            slots = parsed.get("slots", {}) or {}

            # 라벨이 허용 리스트에 없으면 unknown 강제
            if intent not in config.labels:
                intent = "unknown"
                confidence = 0.0

            # threshold 미달도 unknown 처리 (Branch 노드가 'default' 엣지로 보냄)
            if confidence < config.threshold:
                intent = "unknown"

            elapsed_ms = (time.monotonic() - start) * 1000
            log.info(
                "intent.classified",
                intent=intent,
                confidence=confidence,
                latency_ms=round(elapsed_ms, 1),
            )
            return {
                "intent": IntentResult(intent=intent, confidence=confidence, slots=slots),
                "slots": {**state.get("slots", {}), **slots},
            }

        except Exception as exc:
            logging.exception("intent_node failed")
            return {
                "intent": IntentResult(intent="unknown", confidence=0.0, slots={}),
                "errors": [{"node": "intent", "reason": str(exc)[:200]}],
                "fallback_triggered": True,
            }

    return intent_node
