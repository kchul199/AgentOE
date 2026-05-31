"""Unit tests for AuditEmitter (Phase N — N1.3).

격리 전략:
  - AuditRepository 를 Mock 으로 주입 (Mongo 불필요).
  - redis get_redis 를 patch (Redis 불필요).
  - FastAPI Request 는 httpx.Request 로 대체 가능하지만 여기서는 MagicMock 사용.

실행:
    python -m pytest tests/unit/test_audit_emitter.py -v
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.auth import TenantContext
from app.domain.audit_emitter import AUDIT_EVENTS_CHANNEL, AuditEmitter

# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_tenant(
    tenant_id: str = "t-test",
    client_id: str = "c-test",
    roles: list[str] | None = None,
    issuer: str = "agentoe-api",
) -> TenantContext:
    return TenantContext(
        tenant_id=tenant_id,
        client_id=client_id,
        roles=roles or ["operator"],
        issuer=issuer,
    )


def _make_request(
    ip: str = "1.2.3.4",
    user_agent: str = "pytest/1",
    trace_id: str | None = "abc123",
) -> MagicMock:
    req = MagicMock()
    headers: dict[str, str] = {"user-agent": user_agent}
    if trace_id:
        headers["x-trace-id"] = trace_id
    req.headers = headers
    req.client = MagicMock()
    req.client.host = ip
    return req


def _make_repo(doc: dict | None = None) -> AsyncMock:
    """AuditRepository mock — log() 는 항상 doc 반환."""
    repo = AsyncMock()
    repo.log = AsyncMock(return_value=doc or {"metadata": {"action": "test"}, "details": {}})
    return repo


# ── helpers ───────────────────────────────────────────────────────────────────


def _make_redis_mock():
    """aioredis / redis.asyncio 클라이언트 mock."""
    r = AsyncMock()
    r.publish = AsyncMock(return_value=1)
    return r


# ── tests ─────────────────────────────────────────────────────────────────────


class TestAuditEmitterMongo:
    """Mongo insert 경로 검증."""

    @pytest.mark.asyncio
    async def test_emit_calls_repo_log(self):
        """emit() 이 AuditRepository.log() 를 1회 호출하는지."""
        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(
                action="kill_switch.activate",
                actor=_make_tenant(),
                resource={"type": "kill_switch", "id": "tenant:t-test"},
                after={"active": True},
                request=_make_request(),
            )

        repo.log.assert_awaited_once()
        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["action"] == "kill_switch.activate"
        assert call_kwargs["tenant_id"] == "t-test"
        assert call_kwargs["actor_client_id"] == "c-test"
        assert call_kwargs["actor_ip"] == "1.2.3.4"
        assert call_kwargs["trace_id"] == "abc123"
        assert call_kwargs["resource_type"] == "kill_switch"
        assert call_kwargs["resource_id"] == "tenant:t-test"
        assert call_kwargs["after"] == {"active": True}

    @pytest.mark.asyncio
    async def test_emit_system_actor(self):
        """actor=None 이면 tenant_id='system' 으로 처리."""
        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(action="system.startup")

        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["tenant_id"] == "system"
        assert call_kwargs["actor_client_id"] is None

    @pytest.mark.asyncio
    async def test_emit_no_request(self):
        """request=None 이면 IP/UA/trace_id 모두 None — 에러 없이 통과."""
        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(
                action="scenario.create",
                actor=_make_tenant(),
            )

        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["actor_ip"] is None
        assert call_kwargs["actor_user_agent"] is None
        assert call_kwargs["trace_id"] is None

    @pytest.mark.asyncio
    async def test_emit_event_type_fallback(self):
        """event_type 미지정 시 action 으로 fallback."""
        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(action="connector.delete", actor=_make_tenant())

        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["event_type"] == "connector.delete"

    @pytest.mark.asyncio
    async def test_emit_explicit_event_type(self):
        """event_type 명시 시 그대로 전달."""
        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(
                action="kill_switch.activate",
                event_type="kill_switch_activate",
                actor=_make_tenant(),
            )

        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["event_type"] == "kill_switch_activate"


class TestAuditEmitterRedis:
    """Redis publish 경로 검증."""

    @pytest.mark.asyncio
    async def test_emit_publishes_to_channel(self):
        """emit() 이 AUDIT_EVENTS_CHANNEL 에 publish 하는지."""
        repo = _make_repo(doc={"metadata": {"action": "test"}, "details": {}})
        emitter = AuditEmitter(audit_repo=repo)
        redis_mock = _make_redis_mock()

        with patch("app.domain.audit_emitter.get_redis", return_value=redis_mock):
            await emitter.emit(action="admin.tenant_create", actor=_make_tenant())

        redis_mock.publish.assert_awaited_once()
        channel, payload_str = redis_mock.publish.await_args.args
        assert channel == AUDIT_EVENTS_CHANNEL
        # payload 는 JSON 직렬화 가능해야 함
        payload = json.loads(payload_str)
        assert isinstance(payload, dict)

    @pytest.mark.asyncio
    async def test_emit_publishes_fallback_when_mongo_fails(self):
        """Mongo insert 실패(None) 시에도 Redis publish 는 라이트 payload 로 시도."""
        repo = _make_repo(doc=None)  # Mongo 실패 시뮬레이션
        repo.log = AsyncMock(return_value=None)
        emitter = AuditEmitter(audit_repo=repo)
        redis_mock = _make_redis_mock()

        with patch("app.domain.audit_emitter.get_redis", return_value=redis_mock):
            await emitter.emit(action="connector.create", actor=_make_tenant())

        redis_mock.publish.assert_awaited_once()
        _, payload_str = redis_mock.publish.await_args.args
        payload = json.loads(payload_str)
        # fallback payload 는 metadata.action 이라도 있어야 함
        assert payload.get("metadata", {}).get("action") == "connector.create"

    @pytest.mark.asyncio
    async def test_emit_redis_error_does_not_raise(self):
        """Redis publish 실패해도 emit() 이 예외를 전파하지 않음 (Error Handling 규칙)."""
        from redis.exceptions import RedisError

        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)
        redis_mock = _make_redis_mock()
        redis_mock.publish = AsyncMock(side_effect=RedisError("connection refused"))

        with patch("app.domain.audit_emitter.get_redis", return_value=redis_mock):
            # 예외가 나오면 테스트 실패 — None 이어야 함
            result = await emitter.emit(action="scenario.publish", actor=_make_tenant())

        assert result is None  # emit() 은 반환값 없음

    @pytest.mark.asyncio
    async def test_emit_mongo_error_does_not_raise(self):
        """Mongo insert 실패해도 emit() 이 예외를 전파하지 않음 — graceful degradation."""
        repo = _make_repo()
        repo.log = AsyncMock(side_effect=Exception("mongo timeout"))
        emitter = AuditEmitter(audit_repo=repo)

        # Redis 는 get_redis=None 으로 비활성 — Mongo 만 테스트
        with patch("app.domain.audit_emitter.get_redis", None):
            result = await emitter.emit(action="kill_switch.deactivate", actor=_make_tenant())

        assert result is None

    @pytest.mark.asyncio
    async def test_emit_no_redis_package(self):
        """get_redis=None (redis 미설치 환경) 이면 publish 없이 graceful skip."""
        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", None):
            await emitter.emit(action="auth.token_issued", actor=_make_tenant())

        # 여기까지 왔으면 성공 — repo.log 는 여전히 호출됐어야 함
        repo.log.assert_awaited_once()


class TestAuditEmitterPortalIssuer:
    """portal-issuer 토큰의 actor 필드 전달 검증."""

    @pytest.mark.asyncio
    async def test_portal_issuer_preserved(self):
        """portal:operator 토큰의 issuer 가 audit 레코드에 정확히 박히는지."""
        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)
        portal_tenant = _make_tenant(
            roles=["portal:operator"],
            issuer="agentoe-portal",
        )

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(
                action="admin.config_update",
                actor=portal_tenant,
                request=_make_request(),
            )

        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["actor_issuer"] == "agentoe-portal"
        assert "portal:operator" in (call_kwargs["actor_roles"] or [])


class TestAuditEmitterTraceparent:
    """W3C traceparent 헤더 파싱 검증."""

    @pytest.mark.asyncio
    async def test_traceparent_extracted(self):
        """traceparent 헤더에서 trace-id 16바이트 hex 추출."""
        req = MagicMock()
        trace_id_hex = "4bf92f3577b34da6a3ce929d0e0e4736"
        req.headers = {
            "user-agent": "test",
            "traceparent": f"00-{trace_id_hex}-00f067aa0ba902b7-01",
        }
        req.client = None  # x-forwarded-for / host 없음

        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(action="session.start", actor=_make_tenant(), request=req)

        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["trace_id"] == trace_id_hex

    @pytest.mark.asyncio
    async def test_x_trace_id_takes_priority(self):
        """x-trace-id 헤더가 있으면 traceparent 보다 우선."""
        req = MagicMock()
        req.headers = {
            "user-agent": "test",
            "x-trace-id": "custom-trace-999",
            "traceparent": "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01",
        }
        req.client = None

        repo = _make_repo()
        emitter = AuditEmitter(audit_repo=repo)

        with patch("app.domain.audit_emitter.get_redis", return_value=_make_redis_mock()):
            await emitter.emit(action="session.end", actor=_make_tenant(), request=req)

        call_kwargs = repo.log.await_args.kwargs
        assert call_kwargs["trace_id"] == "custom-trace-999"
