"""ops-api Pydantic 스키마"""
from __future__ import annotations
from typing import Any, Literal
from pydantic import BaseModel


# ── 모니터링 ──────────────────────────────────────────────────────────────────

class MetricSnapshot(BaseModel):
    ts: str
    ccu: int                   # 현재 동시 통화 수
    p95_ms: int                # STT+LLM+TTS 합산 P95 (ms)
    p99_ms: int
    error_rate_pct: float      # 전체 에러율 (%)
    slo_achieved_pct: float    # SLO 달성률 (%)
    stt_p95_ms: int
    llm_p95_ms: int
    tts_p95_ms: int
    total_calls_today: int
    failed_calls_today: int

class TimePoint(BaseModel):
    ts: str
    value: float

class MetricHistory(BaseModel):
    ccu: list[TimePoint]
    p95: list[TimePoint]
    error_rate: list[TimePoint]


# ── 환경 설정 ─────────────────────────────────────────────────────────────────

EnvName = Literal["dev", "staging", "prod"]

class EnvConfig(BaseModel):
    env: EnvName
    updated_at: str
    updated_by: str
    values: dict[str, Any]

class EnvConfigUpdate(BaseModel):
    updated_by: str
    values: dict[str, Any]

class ConfigDiff(BaseModel):
    key: str
    dev: Any
    staging: Any
    prod: Any


# ── 상담 이력 ─────────────────────────────────────────────────────────────────

class TraceStep(BaseModel):
    step: str       # stt | llm | tool | tts | intent | transfer
    started_at: str
    duration_ms: int
    status: Literal["ok", "error", "timeout"]
    detail: dict[str, Any] = {}

class SessionSummary(BaseModel):
    session_id: str
    tenant_id: str
    scenario_id: str
    started_at: str
    ended_at: str | None
    duration_s: int | None
    status: Literal["active", "completed", "failed", "transferred"]
    caller_number: str
    turn_count: int
    error_count: int

class SessionDetail(SessionSummary):
    turns: list[dict[str, Any]]
    trace: list[TraceStep]

class SessionListResponse(BaseModel):
    total: int
    page: int
    page_size: int
    items: list[SessionSummary]


# ── Kill Switch ───────────────────────────────────────────────────────────────

class KillSwitch(BaseModel):
    id: str
    scope: str           # tenant | feature | global
    target_id: str
    label: str
    active: bool
    activated_at: str | None
    activated_by: str | None
    reason: str | None

class KillSwitchToggle(BaseModel):
    active: bool
    reason: str
    operator: str


# ── 시나리오 ─────────────────────────────────────────────────────────────────

class ScenarioInfo(BaseModel):
    scenario_id: str
    name: str
    tenant_id: str
    version: int
    published: bool
    tags: list[str]
    updated_at: str
    node_count: int
    env_deployed: dict[str, str]   # env → deployed version

class DeployRequest(BaseModel):
    env: EnvName
    operator: str
    note: str = ""

class TestRequest(BaseModel):
    phone_number: str = "+821012345678"
    mock_asr: str = "안녕하세요"
