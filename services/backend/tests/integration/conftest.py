"""
Integration test infrastructure — in-memory MongoDB + Redis.

mongomock_motor  : Motor-compatible async MongoDB driver (실 네트워크 없음).
fakeredis.aioredis: asyncio Redis backed by in-process dict (실 Redis 없음).

Module-scoped autouse 픽스처가 TestClient lifespan 보다 먼저 활성화되어
init_db / init_redis 를 가로챈다. 다른 통합 테스트 파일은 그대로 동작.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Generator
from datetime import UTC
from unittest.mock import AsyncMock, patch

import fakeredis.aioredis
import mongomock_motor
import pytest
from passlib.context import CryptContext
from starlette.testclient import TestClient

# ── in-memory 싱글턴 ──────────────────────────────────────────────────────────
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)

_FAKE_MONGO: mongomock_motor.AsyncMongoMockClient | None = None
_FAKE_REDIS: fakeredis.aioredis.FakeRedis | None = None

TEST_ADMIN_USER = "portal_test_admin"
TEST_VIEWER_USER = "portal_test_viewer"
TEST_PASSWORD = "Test@Pass123"


def _mongo() -> mongomock_motor.AsyncMongoMockClient:
    global _FAKE_MONGO
    if _FAKE_MONGO is None:
        _FAKE_MONGO = mongomock_motor.AsyncMongoMockClient()
    return _FAKE_MONGO


def _redis() -> fakeredis.aioredis.FakeRedis:
    global _FAKE_REDIS
    if _FAKE_REDIS is None:
        _FAKE_REDIS = fakeredis.aioredis.FakeRedis()
    return _FAKE_REDIS


# ── lifespan 대체 coroutine ────────────────────────────────────────────────────


async def _fake_init_db() -> None:
    import app.core.database as _db_mod

    _db_mod._client = _mongo()  # type: ignore[assignment]


async def _fake_init_redis() -> None:
    import app.core.redis_client as _rc_mod

    _rc_mod._redis = _redis()  # type: ignore[assignment]


# ── 모듈 범위 autouse — TestClient 시작 전에 패치 적용 ────────────────────────
@pytest.fixture(scope="module", autouse=True)
def _integration_infra_stubs():
    mock_grpc = AsyncMock()
    mock_grpc.start = AsyncMock()
    mock_grpc.stop = AsyncMock()

    patches = [
        patch("app.main.init_db", side_effect=_fake_init_db),
        patch("app.main.close_db", new_callable=AsyncMock),
        patch("app.main.init_redis", side_effect=_fake_init_redis),
        patch("app.main.close_redis", new_callable=AsyncMock),
        patch("app.main.GrpcServerLifecycle", return_value=mock_grpc),
        # rate-limit bypass (fakeredis 는 Lua eval 미지원)
        patch(
            "app.middleware.rate_limit_middleware.rate_limit_check",
            new=AsyncMock(return_value=True),
        ),
        # kill-switch 비활성화
        patch(
            "app.middleware.kill_switch_middleware.get_kill_switch_cached",
            new=AsyncMock(return_value=False),
        ),
        # metrics snapshot — Prometheus 불필요
        patch(
            "app.core.metrics.get_metrics_snapshot_async",
            new=AsyncMock(return_value={"ccu": 0, "p95_latency_ms": 0.0, "error_rate": 0.0}),
        ),
        # SSE sleep → 즉시 종료 (스트림 테스트 hang 방지)
        patch(
            "app.api.v1.routers.stream.asyncio.sleep",
            new=AsyncMock(side_effect=asyncio.CancelledError),
        ),
        # 로그인 brute-force 제한 비활성화 (fakeredis INCR 이 module 내 누적됨)
        patch("app.api.v1.routers.auth_portal._check_login_rate_limit", new=AsyncMock()),
    ]
    started = [p.start() for p in patches]
    yield started
    for p in patches:
        with contextlib.suppress(RuntimeError):
            p.stop()


# ── 공용 TestClient (portal 통합 테스트 전용) ─────────────────────────────────
@pytest.fixture(scope="module")
def app_client(_integration_infra_stubs) -> Generator[TestClient, None, None]:
    """Portal 통합 테스트용 FastAPI TestClient — in-memory DB/Redis."""
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


# ── 테스트 사용자 시드 ────────────────────────────────────────────────────────
@pytest.fixture(scope="module", autouse=True)
def seed_test_users(app_client: TestClient) -> Generator[None, None, None]:
    """테스트 전 portal_users 에 admin/viewer 시드, 종료 후 삭제."""
    from datetime import datetime

    from app.core.config import settings

    async def _setup() -> None:
        db = _mongo()[settings.MONGODB_DB_NAME]
        coll = db["portal_users"]
        for username, roles in [
            (TEST_ADMIN_USER, ["portal:admin"]),
            (TEST_VIEWER_USER, ["portal:viewer"]),
        ]:
            await coll.delete_one({"username": username})
            await coll.insert_one(
                {
                    "username": username,
                    "email": f"{username}@test.local",
                    "hashed_password": _pwd_ctx.hash(TEST_PASSWORD),
                    "roles": roles,
                    "is_active": True,
                    "mfa_enabled": False,
                    "mfa_secret_enc": None,
                    "created_at": datetime.now(tz=UTC),
                    "updated_at": datetime.now(tz=UTC),
                }
            )

    async def _teardown() -> None:
        db = _mongo()[settings.MONGODB_DB_NAME]
        coll = db["portal_users"]
        for username in [TEST_ADMIN_USER, TEST_VIEWER_USER]:
            await coll.delete_one({"username": username})

    asyncio.get_event_loop().run_until_complete(_setup())
    yield
    asyncio.get_event_loop().run_until_complete(_teardown())
