"""
CallbotState — LangGraph StateGraph 전역 상태 정의

설계 원칙:
  1. TypedDict 기반 (langgraph의 Reducer 패턴 호환)
  2. messages 는 append-only (add_messages reducer)
  3. 나머지 필드는 replace 전략 (update로 덮어쓰기)
  4. Checkpointer 저장/복구를 위해 모두 JSON 직렬화 가능해야 함
     → bytes / datetime 같은 타입은 여기 담지 말 것
  5. 세션 전반의 컨텍스트(캐릭터/정책/메모리)는 session_meta 에 둔다
"""
from __future__ import annotations

import operator
from typing import Annotated, Any, Literal, TypedDict

# langgraph 가 설치되지 않은 환경에서도 import 가능하도록 try/except
try:
    from langgraph.graph.message import add_messages
except ImportError:  # pragma: no cover — langgraph 미설치 시 placeholder
    def add_messages(left: list, right: list) -> list:
        """Fallback reducer: langgraph 미설치 환경에서도 동작"""
        return (left or []) + (right or [])


# ── 메시지 타입 ──────────────────────────────────────────────────────────────
class Message(TypedDict, total=False):
    """대화 메시지 (user / assistant / system / tool)"""
    role: Literal["user", "assistant", "system", "tool"]
    content: str
    # tool 호출 메타
    tool_name: str | None
    tool_args: dict[str, Any] | None
    tool_result: Any | None
    # 트레이싱
    node_id: str | None
    turn_id: str | None
    ts: str | None  # ISO 8601


# ── 테넌트 / 세션 메타 ───────────────────────────────────────────────────────
class SessionMeta(TypedDict, total=False):
    tenant_id: str
    session_id: str
    scenario_id: str
    scenario_version: int
    caller_number: str | None
    language: str          # 예: "ko-KR"
    policy_level: str      # 예: "strict" | "standard" | "loose"
    persona: dict[str, Any] | None  # 캐릭터 프롬프트, 톤
    feature_flags: dict[str, bool]


# ── 인텐트 결과 ──────────────────────────────────────────────────────────────
class IntentResult(TypedDict, total=False):
    intent: str
    confidence: float
    slots: dict[str, Any]


# ── 그래프 상태 ──────────────────────────────────────────────────────────────
class CallbotState(TypedDict, total=False):
    """
    LangGraph 전역 상태.
    각 노드는 일부 필드만 반환 (partial update) — LangGraph reducer가 병합.
    """
    # 세션/테넌트 메타 (immutable within a turn)
    session: SessionMeta

    # 대화 이력 (append-only, add_messages reducer 사용)
    messages: Annotated[list[Message], add_messages]

    # 현재 턴의 입력/출력
    user_input: str          # STT 결과 (current turn)
    assistant_output: str    # 누적된 LLM 응답 (current turn)

    # 인텐트/슬롯
    intent: IntentResult | None
    slots: dict[str, Any]    # 누적 슬롯 (replace)

    # 흐름 제어
    next_node: str | None            # Branch 노드가 지정
    should_transfer: bool             # 상담원 전환 요청
    should_end: bool                  # 세션 종료
    fallback_triggered: bool          # Tool 실패 → Fallback 진입 여부

    # Tool 호출 이력 (디버깅/감사용)
    tool_calls: Annotated[list[dict[str, Any]], operator.add]

    # 에러/경고 수집 (append-only)
    errors: Annotated[list[dict[str, Any]], operator.add]

    # 성능/비용 측정
    turn_latency_ms: float
    token_usage: dict[str, int]       # {prompt: n, completion: n}
    cost_cents: float


def empty_state(tenant_id: str, session_id: str, scenario_id: str) -> CallbotState:
    """신규 세션용 빈 상태 생성 헬퍼"""
    return CallbotState(
        session=SessionMeta(
            tenant_id=tenant_id,
            session_id=session_id,
            scenario_id=scenario_id,
            scenario_version=1,
            language="ko-KR",
            policy_level="standard",
            feature_flags={},
        ),
        messages=[],
        user_input="",
        assistant_output="",
        intent=None,
        slots={},
        next_node=None,
        should_transfer=False,
        should_end=False,
        fallback_triggered=False,
        tool_calls=[],
        errors=[],
        turn_latency_ms=0.0,
        token_usage={"prompt": 0, "completion": 0},
        cost_cents=0.0,
    )
