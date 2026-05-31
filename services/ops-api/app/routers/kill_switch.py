"""Kill Switch / 기능 플래그 라우터"""
from __future__ import annotations
from datetime import datetime
from fastapi import APIRouter, HTTPException

from app.models import KillSwitch, KillSwitchToggle

router = APIRouter(prefix="/kill-switches", tags=["kill-switch"])

_STORE: dict[str, KillSwitch] = {
    "global:all":           KillSwitch(id="global:all",      scope="global",  target_id="all",           label="전체 서비스 중단",          active=False, activated_at=None, activated_by=None, reason=None),
    "feature:barge_in":     KillSwitch(id="feature:barge_in",scope="feature", target_id="barge_in",      label="바지인(Barge-In)",          active=False, activated_at=None, activated_by=None, reason=None),
    "feature:recording":    KillSwitch(id="feature:recording",scope="feature",target_id="recording",     label="통화 녹취",                  active=False, activated_at=None, activated_by=None, reason=None),
    "feature:llm_fallback": KillSwitch(id="feature:llm_fallback",scope="feature",target_id="llm_fallback",label="LLM Fallback 강제",       active=False, activated_at=None, activated_by=None, reason=None),
    "feature:rate_limit":   KillSwitch(id="feature:rate_limit",scope="feature",target_id="rate_limit",   label="Rate Limit",               active=True,  activated_at="2026-05-12T06:00:00Z", activated_by="charls", reason="트래픽 급증 대비"),
    "tenant:t_acme":        KillSwitch(id="tenant:t_acme",   scope="tenant",  target_id="t_acme",        label="ACME 테넌트 서비스",         active=False, activated_at=None, activated_by=None, reason=None),
    "tenant:t_beta":        KillSwitch(id="tenant:t_beta",   scope="tenant",  target_id="t_beta",        label="Beta 테넌트 서비스",         active=False, activated_at=None, activated_by=None, reason=None),
}


@router.get("", response_model=list[KillSwitch])
async def list_kill_switches() -> list[KillSwitch]:
    return list(_STORE.values())


@router.get("/{ks_id:path}", response_model=KillSwitch)
async def get_kill_switch(ks_id: str) -> KillSwitch:
    if ks_id not in _STORE:
        raise HTTPException(404, f"킬스위치 '{ks_id}' 없음")
    return _STORE[ks_id]


@router.put("/{ks_id:path}", response_model=KillSwitch)
async def toggle_kill_switch(ks_id: str, body: KillSwitchToggle) -> KillSwitch:
    if ks_id not in _STORE:
        raise HTTPException(404, f"킬스위치 '{ks_id}' 없음")
    ks = _STORE[ks_id]
    updated = KillSwitch(
        **{
            **ks.model_dump(),
            "active":       body.active,
            "activated_at": datetime.utcnow().isoformat(timespec="seconds") + "Z" if body.active else None,
            "activated_by": body.operator if body.active else None,
            "reason":       body.reason if body.active else None,
        }
    )
    _STORE[ks_id] = updated
    return updated
