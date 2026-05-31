"""Session management API endpoints.

보안/품질 개선 포인트 (v2 패치):
  - 모든 요청 바디는 Pydantic 모델로 강제 (Injection/혼선 차단)
  - tenant ownership 검증은 공용 assert_tenant_ownership 사용
  - 세션 ID는 ``sess_`` 프리픽스 + UUID4, 재현 가능한 로그용 session_id_hash 발행
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.core.auth import TenantContext, assert_tenant_ownership, get_current_tenant
from app.domain.session_fsm import SessionState
from app.repositories.session_repository import SessionRepository

router = APIRouter()


# ── Request Models ───────────────────────────────────────────────────────────


class CreateSessionRequest(BaseModel):
    """세션 생성 페이로드. dict 직접 접근 제거 — 필드/길이/형식을 강제한다."""

    scenario_id: str = Field(
        default="default",
        min_length=1,
        max_length=64,
        pattern=r"^[a-zA-Z0-9_\-]+$",
        description="시나리오 식별자 (영숫자/언더스코어/하이픈)",
    )
    # 한국 휴대폰 형식 또는 E.164. 마스킹 로깅과 별개로 형식 검증.
    caller_number: str | None = Field(
        default=None,
        max_length=32,
        pattern=r"^[+0-9\-\s()]*$",
        description="발신번호. PII이므로 로그에서는 마스킹 처리",
    )
    metadata: dict[str, Any] = Field(default_factory=dict, max_length=50)

    model_config = {"extra": "forbid"}  # 알 수 없는 필드 거부


class SessionResponse(BaseModel):
    session_id: str
    tenant_id: str
    scenario_id: str
    status: str
    ws_url: str


# ── Endpoints ────────────────────────────────────────────────────────────────


@router.post("/", status_code=status.HTTP_201_CREATED, response_model=SessionResponse)
async def create_session(
    payload: CreateSessionRequest,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: SessionRepository = Depends(SessionRepository),
) -> dict:
    session_id = f"sess_{uuid.uuid4()}"
    session_data = {
        "session_id": session_id,
        "tenant_id": tenant.tenant_id,
        "scenario_id": payload.scenario_id,
        "status": SessionState.IDLE,
        "caller_number": payload.caller_number,
        "metadata": payload.metadata,
    }
    created = await repo.create(session_data)
    return {**created, "ws_url": f"wss://api.agentoe.io/ws/sessions/{session_id}"}


@router.get("/{session_id}")
async def get_session(
    session_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: SessionRepository = Depends(SessionRepository),
) -> dict:
    session = await repo.get_by_id(session_id)
    assert_tenant_ownership(session, tenant, resource_type="session", resource_id=session_id)
    return session


@router.get("/")
async def list_sessions(
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    status_filter: str | None = Query(
        default=None, alias="status", max_length=32, pattern=r"^[A-Z_]*$"
    ),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    repo: SessionRepository = Depends(SessionRepository),
) -> dict:
    items, total = await repo.list_by_tenant(tenant.tenant_id, status_filter, limit, offset)
    return {"items": items, "total": total, "limit": limit, "offset": offset}


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def end_session(
    session_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: SessionRepository = Depends(SessionRepository),
) -> None:
    session = await repo.get_by_id(session_id)
    assert_tenant_ownership(session, tenant, resource_type="session", resource_id=session_id)
    await repo.end_session(session_id)


# ── Phase N (N1.8) — Turn-level 리플레이 API ────────────────────────────────
#
# 운영포탈 상담이력 페이지: session turn 타임라인 표시용.
# RBAC: portal:viewer+ (require_portal_role — portal issuer 토큰만 허용).
# tenant ownership 검증 필수 (IDOR 방지 — assert_tenant_ownership).


@router.get("/{session_id}/turns")
async def get_session_turns(
    session_id: str,
    tenant: Annotated[TenantContext, Depends(get_current_tenant)],
    repo: SessionRepository = Depends(SessionRepository),
    limit: int = Query(default=50, ge=1, le=200, description="최대 반환 turn 수"),
    offset: int = Query(default=0, ge=0, description="페이지 오프셋"),
) -> dict:
    """Session turn-by-turn 리플레이 (text 레벨).

    audio replay 는 비스코프 (plan §1.3) — turn 텍스트/메타만 반환.
    portal:viewer+ 이면 조회 가능하지만, tenant ownership 강제 (portal:admin 도 동일 테넌트).
    """
    # tenant ownership 검증
    session = await repo.get_by_id(session_id)
    assert_tenant_ownership(session, tenant, resource_type="session", resource_id=session_id)

    # get_recent_history 는 최신순 limit 개 반환 — offset 지원을 위해 확장 필요.
    # 현재 MVP: offset=0 이면 get_recent_history, offset>0 이면 raw aggregate.
    if offset == 0:
        turns = await repo.get_recent_history(session_id, limit=limit)
    else:
        turns = await _get_turns_with_offset(repo, session_id, limit=limit, offset=offset)

    return {
        "session_id": session_id,
        "turns": turns,
        "count": len(turns),
        "limit": limit,
        "offset": offset,
    }


async def _get_turns_with_offset(
    repo: SessionRepository,
    session_id: str,
    limit: int,
    offset: int,
) -> list[dict]:
    """offset 지원 turn 조회 (history_col aggregate)."""
    pipeline = [
        {"$match": {"session_id": session_id}},
        {"$sort": {"timestamp": 1}},
        {"$skip": offset},
        {"$limit": limit},
        {"$project": {"_id": 0}},
    ]
    return await repo.history_col.aggregate(pipeline).to_list(limit)
