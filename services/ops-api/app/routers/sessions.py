"""상담 이력 & 로그 트레이스 라우터"""
from __future__ import annotations
import random
from datetime import datetime, timedelta
from fastapi import APIRouter, Query

from app.models import SessionSummary, SessionDetail, SessionListResponse, TraceStep

router = APIRouter(prefix="/sessions", tags=["sessions"])

_STATUSES = ["completed", "completed", "completed", "failed", "transferred"]
_SCENARIOS = ["greet_v2", "billing_inquiry", "service_cancel", "tech_support", "faq_general"]
_TENANTS = ["t_acme", "t_beta", "t_gamma"]


def _fake_session(offset_min: int) -> SessionSummary:
    status = random.choice(_STATUSES)
    started = datetime.utcnow() - timedelta(minutes=offset_min)
    dur = random.randint(30, 420)
    return SessionSummary(
        session_id=f"sess_{abs(hash(offset_min + random.random())) % 10**10:010d}",
        tenant_id=random.choice(_TENANTS),
        scenario_id=random.choice(_SCENARIOS),
        started_at=started.isoformat(timespec="seconds") + "Z",
        ended_at=(started + timedelta(seconds=dur)).isoformat(timespec="seconds") + "Z",
        duration_s=dur,
        status=status,
        caller_number=f"+8210{random.randint(10000000, 99999999)}",
        turn_count=random.randint(2, 12),
        error_count=0 if status == "completed" else random.randint(1, 3),
    )


def _fake_trace(session_id: str) -> list[TraceStep]:
    steps = []
    t = datetime.utcnow() - timedelta(seconds=180)

    def _add(step: str, dur: int, status: str = "ok", detail: dict | None = None):
        nonlocal t
        steps.append(TraceStep(
            step=step,
            started_at=t.isoformat(timespec="milliseconds") + "Z",
            duration_ms=dur,
            status=status,
            detail=detail or {},
        ))
        t += timedelta(milliseconds=dur)

    _add("stt", 240, detail={"text": "안녕하세요", "confidence": 0.97})
    _add("intent", 35, detail={"intent": "greeting", "score": 0.94})
    _add("llm", 620, detail={"model": "claude-sonnet-4-6", "tokens_in": 312, "tokens_out": 48})
    _add("tts", 110, detail={"chars": 24, "voice": "ko-KR-Wavenet-A"})
    _add("stt", 310, detail={"text": "요금 조회하고 싶어요", "confidence": 0.91})
    _add("intent", 32, detail={"intent": "billing_inquiry", "score": 0.89})
    _add("llm", 780, detail={"model": "claude-sonnet-4-6", "tokens_in": 890, "tokens_out": 95})
    _add("tool", 430, detail={"tool": "get_billing_info", "status": "ok", "result_size": 312})
    _add("llm", 510, detail={"model": "claude-sonnet-4-6", "tokens_in": 1200, "tokens_out": 82})
    _add("tts", 140, detail={"chars": 58, "voice": "ko-KR-Wavenet-A"})
    return steps


@router.get("", response_model=SessionListResponse)
async def list_sessions(
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    status: str | None = None,
    tenant_id: str | None = None,
) -> SessionListResponse:
    total = 347
    items = [_fake_session(i * 3 + random.randint(0, 10)) for i in range(page_size)]
    if status:
        items = [s for s in items if s.status == status]
    if tenant_id:
        items = [s for s in items if s.tenant_id == tenant_id]
    return SessionListResponse(total=total, page=page, page_size=page_size, items=items)


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(session_id: str) -> SessionDetail:
    summary = _fake_session(random.randint(5, 60))
    summary.session_id = session_id
    turns = [
        {"turn": 1, "role": "bot",  "text": "안녕하세요. 무엇을 도와드릴까요?"},
        {"turn": 2, "role": "user", "text": "안녕하세요"},
        {"turn": 3, "role": "bot",  "text": "네, 반갑습니다. 어떤 도움이 필요하신가요?"},
        {"turn": 4, "role": "user", "text": "요금 조회하고 싶어요"},
        {"turn": 5, "role": "bot",  "text": "고객님의 이번 달 청구 금액은 45,000원입니다."},
    ]
    return SessionDetail(**summary.model_dump(), turns=turns, trace=_fake_trace(session_id))
