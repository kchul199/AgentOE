"""Phase N audit emitter — Mongo TS insert + Redis publish 통합 헬퍼.

플랜: docs/guide/phase-N-ops-portal-plan.md §2.6.

설계 원칙:
  1. **Graceful degradation** — Mongo 실패해도 Redis 만, Redis 실패해도 Mongo 만.
     양쪽 다 실패해도 메인 통화/요청 흐름 절대 방해 X (Performance First + Error Handling).
  2. **Request-bound 자동 추출** — actor_ip / actor_user_agent / trace_id 를 FastAPI Request
     객체에서 자동 추출. 라우터에서 한 줄로 호출 가능하게.
  3. **TenantContext 자동 매핑** — actor_client_id / actor_roles / actor_issuer 를
     `core.auth.TenantContext` 에서 끌어옴.
  4. **Redis 채널** — `agentoe:events:audit`. SSE fan-out (N1.4) 이 같은 채널 구독.
     payload 는 JSON 직렬화된 audit doc.

호출 패턴 (라우터):
    emitter = Depends(get_audit_emitter)
    await emitter.emit(
        action="kill_switch.toggle",
        actor=tenant,
        resource={"type": "kill_switch", "id": switch_id},
        before={"active": False}, after={"active": True},
        request=request,
        event_type="kill_switch_activate",  # 옛 audit query path 호환용
    )
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import structlog
from fastapi import Depends, Request

from app.core.auth import TenantContext
from app.core.config import settings
from app.repositories.audit_repository import AuditRepository

try:
    from redis.exceptions import RedisError  # type: ignore

    from app.core.redis_client import get_redis
except ImportError:  # 테스트 환경 redis 미설치 graceful
    get_redis = None  # type: ignore
    RedisError = Exception  # type: ignore

logger = structlog.get_logger(__name__)

# Redis pub/sub 채널 — SSE fan-out (N1.4 broadcaster) 이 같은 키 구독.
AUDIT_EVENTS_CHANNEL = "agentoe:events:audit"


# ── env 식별 ────────────────────────────────────────────────────────────────
#
# AppSettings 에 ENVIRONMENT 또는 ENV 필드가 어떻게 정의되어 있든 안전하게 추출.
# 운영포탈 의 env switcher 가 audit 의 metadata.env 로 필터링하기 때문에 잘못된
# 값을 박으면 portal 의 env 격리가 깨짐.


def _current_env() -> str | None:
    for attr in ("ENVIRONMENT", "ENV", "APP_ENV"):
        v = getattr(settings, attr, None)
        if v:
            return str(v).lower()
    return None


# ── Audit Emitter ────────────────────────────────────────────────────────────


class AuditEmitter:
    """Mongo TS insert + Redis publish 통합. 라우터에 Depends 로 주입.

    `audit_repo` 가 None 이면 default `AuditRepository()` 인스턴스. 테스트에서
    repo mock 을 주입 가능하게 keyword-only.
    """

    def __init__(self, audit_repo: AuditRepository | None = None) -> None:
        self._repo = audit_repo or AuditRepository()

    async def emit(
        self,
        *,
        action: str,
        actor: TenantContext | None = None,
        resource: dict[str, Any] | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        request: Request | None = None,
        event_type: str | None = None,
        session_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """단일 emit — Mongo insert + Redis publish 동시.

        - `action`: dot-namespaced ("kill_switch.toggle", "scenario.publish" 등). 필수.
        - `actor`: TenantContext — None 인 경우 system 액션.
        - `resource`: {"type": str, "id": str} — 영향 받은 리소스.
        - `before/after`: state diff snapshot — write 액션 한정.
        - `request`: FastAPI Request — IP/User-Agent/trace_id 자동 추출.
        - `event_type`: 옛 audit query 호환용 (없으면 action 사용).
        """
        # --- actor 정보 추출 -----------------------------------------------
        tenant_id = "system"
        actor_client_id: str | None = None
        actor_roles: list[str] | None = None
        actor_issuer: str | None = None
        if actor is not None:
            tenant_id = actor.tenant_id
            actor_client_id = actor.client_id
            actor_roles = list(actor.roles) if actor.roles else None
            actor_issuer = actor.issuer

        # --- request 정보 추출 (graceful — request 없어도 OK) -------------
        actor_ip: str | None = None
        actor_user_agent: str | None = None
        trace_id: str | None = None
        if request is not None:
            actor_ip = _extract_client_ip(request)
            actor_user_agent = request.headers.get("user-agent")
            trace_id = request.headers.get("x-trace-id") or _extract_traceparent(request)

        # --- resource ------------------------------------------------------
        resource_type: str | None = None
        resource_id: str | None = None
        if resource is not None:
            resource_type = resource.get("type")
            resource_id = resource.get("id")

        # --- 1) Mongo TS insert -------------------------------------------
        # AuditRepository.log() 가 내부 insert 예외를 삼키지만, repo 자체가 실패해도
        # 메인 흐름이 끊기면 안 됨 (Performance First / Error Handling 규칙).
        doc: dict[str, Any] | None = None
        try:
            doc = await self._repo.log(
                event_type=event_type or action,
                tenant_id=tenant_id,
                session_id=session_id,
                actor=actor_client_id or "system",  # 기존 actor:str 필드 backward-compat
                details=details,
                env=_current_env(),
                actor_client_id=actor_client_id,
                actor_roles=actor_roles,
                actor_ip=actor_ip,
                actor_user_agent=actor_user_agent,
                actor_issuer=actor_issuer,
                action=action,
                trace_id=trace_id,
                resource_type=resource_type,
                resource_id=resource_id,
                before=before,
                after=after,
            )
        except Exception as e:
            logger.error("audit_repo_log_failed", error=str(e), action=action)

        # --- 2) Redis publish (SSE fan-out 채널) --------------------------
        # Mongo insert 가 None (실패) 여도 Redis publish 는 시도 — 어느 한쪽이 살아있으면
        # 운영자 SSE 는 받음 + Grafana Loki / 외부 sink 도 받을 수 있음.
        if get_redis is None:
            return  # redis 패키지 미설치 (테스트) — silent skip

        try:
            r = get_redis()
        except Exception as e:
            logger.warning("audit_redis_unavailable", error=str(e), action=action)
            return

        # publish payload 는 doc 이 None 이면 (Mongo 실패) 라이트 버전이라도 보내고
        # event_type/action 정도는 운영자가 보게.
        payload = doc or {
            "metadata": {
                "tenant_id": tenant_id,
                "action": action,
                "event_type": event_type or action,
                "actor_client_id": actor_client_id,
                "env": _current_env(),
            }
        }

        try:
            await r.publish(AUDIT_EVENTS_CHANNEL, _json_dumps(payload))
        except RedisError as e:
            logger.warning("audit_redis_publish_failed", error=str(e), action=action)
        except Exception as e:
            # JSON 직렬화 실패 등 — defensive
            logger.warning("audit_publish_unknown_error", error=str(e), action=action)


# ── 헬퍼 ─────────────────────────────────────────────────────────────────────


def _extract_client_ip(request: Request) -> str | None:
    """ALB X-Forwarded-For 또는 직접 client.host. 첫 hop 만."""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    real = request.headers.get("x-real-ip")
    if real:
        return real
    if request.client:
        return request.client.host
    return None


def _extract_traceparent(request: Request) -> str | None:
    """W3C traceparent 헤더에서 trace-id 부 (16 bytes hex)."""
    tp = request.headers.get("traceparent")
    if not tp:
        return None
    parts = tp.split("-")
    if len(parts) >= 2 and len(parts[1]) == 32:
        return parts[1]
    return None


def _json_dumps(doc: dict[str, Any]) -> str:
    """datetime 등 비-JSON 객체를 isoformat 으로 직렬화."""
    return json.dumps(doc, default=_json_default, ensure_ascii=False)


def _json_default(obj: Any) -> Any:
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if hasattr(obj, "__str__"):
        return str(obj)
    raise TypeError(f"non-serializable: {type(obj)}")


# ── FastAPI Depends ─────────────────────────────────────────────────────────


def get_audit_emitter(
    audit_repo: Annotated[AuditRepository, Depends(AuditRepository)],
) -> AuditEmitter:
    """라우터에서 `Depends(get_audit_emitter)` 로 주입.

    AuditRepository 도 같이 주입되어 Mongo connection pool 공유.
    """
    return AuditEmitter(audit_repo=audit_repo)
