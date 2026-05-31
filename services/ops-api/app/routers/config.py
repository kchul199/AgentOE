"""환경별 설정 관리 라우터 (dev / staging / prod)"""
from __future__ import annotations
from copy import deepcopy
from datetime import datetime
from fastapi import APIRouter, HTTPException

from app.models import EnvConfig, EnvConfigUpdate, ConfigDiff, EnvName

router = APIRouter(prefix="/config", tags=["config"])

# ── 인메모리 목 저장소 ──────────────────────────────────────────────────────
_BASE_VALUES: dict[str, object] = {
    # LLM
    "LLM_MODEL":              "claude-sonnet-4-6",
    "LLM_MAX_TOKENS":         1024,
    "LLM_TEMPERATURE":        0.3,
    "LLM_TIMEOUT_S":          10,
    # STT
    "STT_PROVIDER":           "google",
    "STT_LANGUAGE":           "ko-KR",
    "STT_SAMPLE_RATE":        8000,
    # TTS
    "TTS_PROVIDER":           "google",
    "TTS_VOICE":              "ko-KR-Wavenet-A",
    "TTS_SPEAKING_RATE":      1.0,
    # 성능
    "MAX_CONCURRENT_CALLS":   100,
    "SESSION_TIMEOUT_S":      300,
    "BARGE_IN_ENABLED":       True,
    "VAD_THRESHOLD":          0.5,
    # Rate limit
    "RATE_LIMIT_RPM":         600,
    "RATE_LIMIT_ENABLED":     True,
    # 비즈니스
    "DEFAULT_GREETING":       "안녕하세요. 무엇을 도와드릴까요?",
    "TRANSFER_QUEUE":         "agt_general",
    "RECORDING_ENABLED":      True,
}

_STORE: dict[EnvName, EnvConfig] = {
    "dev": EnvConfig(
        env="dev",
        updated_at="2026-05-10T09:00:00Z",
        updated_by="charls",
        values={
            **_BASE_VALUES,
            "LLM_MODEL": "claude-haiku-4-5-20251001",
            "MAX_CONCURRENT_CALLS": 10,
            "RATE_LIMIT_RPM": 60,
            "RECORDING_ENABLED": False,
        },
    ),
    "staging": EnvConfig(
        env="staging",
        updated_at="2026-05-11T14:30:00Z",
        updated_by="charls",
        values={
            **_BASE_VALUES,
            "LLM_MODEL": "claude-sonnet-4-6",
            "MAX_CONCURRENT_CALLS": 50,
        },
    ),
    "prod": EnvConfig(
        env="prod",
        updated_at="2026-05-12T08:00:00Z",
        updated_by="charls",
        values=deepcopy(_BASE_VALUES),
    ),
}


@router.get("/environments")
async def list_environments() -> list[str]:
    return list(_STORE.keys())


@router.get("/{env}", response_model=EnvConfig)
async def get_config(env: EnvName) -> EnvConfig:
    if env not in _STORE:
        raise HTTPException(404, f"환경 '{env}' 없음")
    return _STORE[env]


@router.put("/{env}", response_model=EnvConfig)
async def update_config(env: EnvName, body: EnvConfigUpdate) -> EnvConfig:
    if env not in _STORE:
        raise HTTPException(404, f"환경 '{env}' 없음")
    existing = _STORE[env]
    updated = EnvConfig(
        env=env,
        updated_at=datetime.utcnow().isoformat(timespec="seconds") + "Z",
        updated_by=body.updated_by,
        values={**existing.values, **body.values},
    )
    _STORE[env] = updated
    return updated


@router.get("/diff/all", response_model=list[ConfigDiff])
async def diff_all() -> list[ConfigDiff]:
    """세 환경 간 값이 다른 키만 반환"""
    all_keys = set()
    for cfg in _STORE.values():
        all_keys.update(cfg.values.keys())

    diffs: list[ConfigDiff] = []
    for key in sorted(all_keys):
        dev_val     = _STORE["dev"].values.get(key)
        staging_val = _STORE["staging"].values.get(key)
        prod_val    = _STORE["prod"].values.get(key)
        if not (dev_val == staging_val == prod_val):
            diffs.append(ConfigDiff(key=key, dev=dev_val, staging=staging_val, prod=prod_val))
    return diffs
