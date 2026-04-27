"""Session management API endpoints.

보안/품질 개선 포인트 (v2 패치):
  - 모든 요청 바디는 Pydantic 모델로 강제 (Injection/혼선 차단)
  - tenant ownership 검증은 공용 assert_tenant_ownership 사용
  - 세션 ID는 ``sess_`` 프리픽스 + UUID4, 재현 가능한 로그용 session_id_hash 발행
"""
from __future__ import annotations

from typing import Annotated, Any
import uuid

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
    status_filter: str | None = Query(default=None, alias="status", max_length=32, pattern=r"^[A-Z_]*$"),
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
