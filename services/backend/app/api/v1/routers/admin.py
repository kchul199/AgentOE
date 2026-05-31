"""Admin API — Tenant management (super_admin only) + AM proxy (portal:operator+).

Phase N (N1.3): write 경로 (create / update / delete) 에 audit emit 추가.
Phase N (N1.6): AM proxy — GET /admin/alerts, POST /admin/alerts/silence,
                DELETE /admin/alerts/silence/{id}.
Phase N (N2.1): GET /admin/env/info — 환경 식별 + 빌드 정보 (portal:viewer+).
Phase N (N5.1): 환경별 설정 CRUD — GET/PUT /admin/config/{env}, GET /admin/config/diff.
read 경로 (list / get) 는 audit 대상 아님 — 양이 너무 많고 분석가치 낮음.
"""

import contextlib
import hmac
import os
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.core.auth import TenantContext, require_portal_role, require_roles
from app.core.database import get_database
from app.domain.audit_emitter import AuditEmitter, get_audit_emitter
from app.infra.alertmanager_client import get_alertmanager_client
from app.repositories.tenant_repository import TenantRepository

_VALID_ENVS = {"dev", "staging", "prod"}

router = APIRouter()


# ── CSRF double-submit 검증 (portal cookie auth 경로 전용) ────────────────────


async def _require_csrf(
    request: Request,
    csrf_cookie: Annotated[str | None, Cookie(alias="__csrf__")] = None,
) -> None:
    """State-changing admin 엔드포인트용 CSRF 검증.

    portal SPA 는 httponly portal_access 쿠키로 인증하므로, 상태 변경 요청에
    double-submit CSRF 패턴을 적용한다.
    Authorization 헤더 직접 전달(API key 방식) 시에도 동일하게 검증 — 일관성.
    """
    header_val = request.headers.get("X-CSRF-Token")
    if not header_val or not csrf_cookie or not hmac.compare_digest(header_val, csrf_cookie):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )


# ── N2.1: 환경 정보 엔드포인트 ──────────────────────────────────────────────


@router.get("/env/info")
async def get_env_info(
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
) -> dict:
    """운영 환경 식별 정보 (portal:viewer+).

    반환:
        environment — ENVIRONMENT 설정값 (production / staging / development)
        git_sha     — GIT_SHA 환경변수 (CI 가 주입, 없으면 "unknown")
        build_time  — BUILD_TIME 환경변수 (ISO 8601, 없으면 None)
        server_time — 현재 서버 UTC 시각 (ISO 8601)
        pod_name    — POD_NAME (k8s downward API, 없으면 hostname)
    """
    import socket

    from app.core.config import settings

    return {
        "environment": getattr(settings, "ENVIRONMENT", "unknown"),
        "git_sha": os.environ.get("GIT_SHA", "unknown"),
        "build_time": os.environ.get("BUILD_TIME"),
        "server_time": datetime.now(UTC).isoformat(),
        "pod_name": os.environ.get("POD_NAME", socket.gethostname()),
    }


@router.get("/tenants")
async def list_tenants(
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
) -> dict:
    items = await repo.list_all()
    return {"items": items, "total": len(items)}


@router.post("/tenants", status_code=status.HTTP_201_CREATED)
async def create_tenant(
    payload: dict,
    request: Request,
    tenant: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    result = await repo.create(payload)
    if audit is not None:
        await audit.emit(
            action="tenant.create",
            event_type="tenant_create",
            actor=tenant,
            resource={
                "type": "tenant",
                "id": result.get("tenant_id") or payload.get("tenant_id", ""),
            },
            after=result,
            request=request,
        )
    return result


@router.get("/tenants/{tenant_id}")
async def get_tenant(
    tenant_id: str,
    caller: Annotated[TenantContext, Depends(require_roles("super_admin", "admin"))],
    repo: TenantRepository = Depends(TenantRepository),
) -> dict:
    return await repo.get_by_id(tenant_id)


@router.patch("/tenants/{tenant_id}")
async def update_tenant(
    tenant_id: str,
    payload: dict,
    request: Request,
    caller: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    # before snapshot — best-effort (실패 시 None)
    before: dict | None = None
    try:
        before = await repo.get_by_id(tenant_id)
    except Exception:
        before = None

    result = await repo.update(tenant_id, payload)

    if audit is not None:
        await audit.emit(
            action="tenant.update",
            event_type="tenant_update",
            actor=caller,
            resource={"type": "tenant", "id": tenant_id},
            before=before,
            after=result,
            request=request,
        )
    return result


@router.delete("/tenants/{tenant_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tenant(
    tenant_id: str,
    request: Request,
    caller: Annotated[TenantContext, Depends(require_roles("super_admin"))],
    repo: TenantRepository = Depends(TenantRepository),
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> None:
    # before snapshot
    before: dict | None = None
    try:
        before = await repo.get_by_id(tenant_id)
    except Exception:
        before = None

    await repo.delete(tenant_id)

    if audit is not None:
        await audit.emit(
            action="tenant.delete",
            event_type="tenant_delete",
            actor=caller,
            resource={"type": "tenant", "id": tenant_id},
            before=before,
            request=request,
        )


# ── Phase N (N1.6) — Alertmanager proxy ─────────────────────────────────────
#
# portal:operator+ 만 접근. AM 직접 노출 대신 backend 가 basic auth + audit 담당.


@router.get("/alerts")
async def get_alerts(
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
    active: bool = True,
) -> dict:
    """현재 firing/pending Alertmanager 알람 목록."""
    am = get_alertmanager_client()
    alerts = await am.get_alerts(active=active)
    return {"alerts": alerts, "total": len(alerts)}


@router.post("/alerts/silence", status_code=status.HTTP_201_CREATED)
async def create_silence(
    payload: dict,
    request: Request,
    tenant: Annotated[
        TenantContext, Depends(require_portal_role("portal:operator", "portal:admin"))
    ],
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """Alertmanager silence 생성 (portal:operator+). audit emit 포함."""
    am = get_alertmanager_client()
    try:
        result = await am.create_silence(payload)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Alertmanager error: {e}") from e

    if audit is not None:
        silence_id = result.get("silenceID", "")
        await audit.emit(
            action="alert.silence_create",
            event_type="alert_silence_create",
            actor=tenant,
            resource={"type": "silence", "id": silence_id},
            after=payload,
            request=request,
        )
    return result


@router.delete("/alerts/silence/{silence_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_silence(
    silence_id: str,
    request: Request,
    tenant: Annotated[
        TenantContext, Depends(require_portal_role("portal:operator", "portal:admin"))
    ],
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> None:
    """Alertmanager silence 해제 (portal:operator+). audit emit 포함."""
    am = get_alertmanager_client()
    try:
        await am.delete_silence(silence_id)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Alertmanager error: {e}") from e

    if audit is not None:
        await audit.emit(
            action="alert.silence_delete",
            event_type="alert_silence_delete",
            actor=tenant,
            resource={"type": "silence", "id": silence_id},
            request=request,
        )


# ── Phase N (N5.1) — 환경별 설정 CRUD ─────────────────────────────────────────
#
# portal_configs 컬렉션 스키마:
#   { env: "dev"|"staging"|"prod", values: {key: str}, updated_by: str, updated_at: ISO }
#
# RBAC:
#   GET  /config/diff        — portal:viewer+
#   GET  /config/{env}       — portal:viewer+
#   PUT  /config/dev|staging — portal:operator+
#   PUT  /config/prod        — portal:admin 전용 (추가 체크)


class ConfigUpdateBody(BaseModel):
    updated_by: str
    values: dict[str, str]


# ★ /config/diff 를 /config/{env} 보다 먼저 선언해야 라우트 섀도잉 없음
@router.get("/config/diff")
async def get_config_diff(
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
) -> dict:
    """3개 환경 설정 비교 — 값이 다른 키 목록 반환 (portal:viewer+).

    반환:
        diffs  — list[{ key, dev, staging, prod }]  값이 다른 키만
        total  — 차이 키 수
    """
    db = get_database()
    cursor = db["portal_configs"].find({}, {"_id": 0})
    docs: dict[str, dict] = {
        doc["env"]: doc async for doc in cursor if doc.get("env") in _VALID_ENVS
    }

    all_keys: set[str] = set()
    for doc in docs.values():
        all_keys.update(doc.get("values", {}).keys())

    diffs = []
    for key in sorted(all_keys):
        vals = {
            env: docs.get(env, {}).get("values", {}).get(key) for env in ("dev", "staging", "prod")
        }
        # 값(None 포함) 의 문자열 표현이 모두 같으면 diff 아님
        if len({str(v) for v in vals.values()}) > 1:
            diffs.append({"key": key, **vals})

    return {"diffs": diffs, "total": len(diffs)}


@router.get("/config/{env}")
async def get_config(
    env: str,
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
) -> dict:
    """환경별 설정 조회 (portal:viewer+).

    반환:
        env        — "dev" | "staging" | "prod"
        values     — { key: str }
        updated_by — 마지막 편집자
        updated_at — ISO 8601 UTC
    """
    if env not in _VALID_ENVS:
        raise HTTPException(
            status_code=400, detail=f"Invalid env: {env!r}. Must be dev|staging|prod."
        )

    db = get_database()
    doc = await db["portal_configs"].find_one({"env": env}, {"_id": 0})
    if doc is None:
        # 아직 저장된 설정 없음 — 빈 구조 반환
        doc = {
            "env": env,
            "values": {},
            "updated_by": "",
            "updated_at": datetime.now(UTC).isoformat(),
        }
    return doc


@router.put("/config/{env}")
async def update_config(
    env: str,
    payload: ConfigUpdateBody,
    request: Request,
    tenant: Annotated[
        TenantContext, Depends(require_portal_role("portal:operator", "portal:admin"))
    ],
    _csrf: Annotated[None, Depends(_require_csrf)],
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """환경별 설정 저장 (portal:operator+; prod 는 portal:admin 전용).

    요청 body:
        updated_by — 편집자 식별자 (사용자 ID / 이메일)
        values     — { key: str }  전체 설정 맵 (replace semantics)

    반환: 저장된 설정 문서 (get_config 와 동일 구조)
    """
    if env not in _VALID_ENVS:
        raise HTTPException(
            status_code=400, detail=f"Invalid env: {env!r}. Must be dev|staging|prod."
        )

    # prod 는 portal:admin 전용 추가 검증
    if env == "prod" and "portal:admin" not in (tenant.roles or []):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Updating prod config requires portal:admin role.",
        )

    db = get_database()
    now_iso = datetime.now(UTC).isoformat()

    # before snapshot (audit 용)
    before: dict | None = None
    with contextlib.suppress(Exception):
        before = await db["portal_configs"].find_one({"env": env}, {"_id": 0})

    doc = {
        "env": env,
        "values": payload.values,
        "updated_by": payload.updated_by,
        "updated_at": now_iso,
    }

    await db["portal_configs"].find_one_and_replace(
        {"env": env},
        doc,
        upsert=True,
    )

    if audit is not None:
        await audit.emit(
            action="config.update",
            event_type="config_update",
            actor=tenant,
            resource={"type": "config", "id": env},
            before=before,
            after=doc,
            request=request,
        )

    return doc
