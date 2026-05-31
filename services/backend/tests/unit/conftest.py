"""
Unit test infrastructure stubs.

모든 단위 테스트에서 실제 MongoDB / Redis 드라이버 연결을 차단한다.

전략:
  - `app.core.database._client` 를 MagicMock으로 교체 →
    `get_database()` 가 mock 컬렉션을 반환. 모든 `from ... import get_database`
    바인딩에서 동작 (함수는 모듈 글로벌을 이름으로 참조함).
  - `app.core.redis_client._redis` 를 AsyncMock으로 교체 →
    `get_redis()` 가 mock 클라이언트를 반환.
  - `app.main.{init_db,close_db,init_redis,close_redis}` 를 no-op AsyncMock으로
    교체 → TestClient(app) lifespan 시 실제 연결을 시도하지 않음.
  - gRPC 서버를 mock으로 교체 → 포트 바인딩 없이 lifespan 통과.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture(autouse=True)
def _unit_infra_stubs():
    """Stub DB + Redis + gRPC for every unit test (autouse)."""

    # ── Mock DB ──────────────────────────────────────────────────────────────
    mock_client = MagicMock()
    # mock_client[db_name] → mock_db (MagicMock supports __getitem__)

    # ── Mock Redis ───────────────────────────────────────────────────────────
    mock_redis = AsyncMock()
    mock_redis.get = AsyncMock(return_value=None)
    mock_redis.setex = AsyncMock(return_value=True)
    mock_redis.set = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)
    mock_redis.ping = AsyncMock(return_value=True)
    mock_redis.eval = AsyncMock(return_value=None)
    mock_redis.xadd = AsyncMock(return_value="0-1")
    mock_redis.xread = AsyncMock(return_value=[])
    mock_redis.expire = AsyncMock(return_value=True)

    # ── Mock gRPC lifecycle ───────────────────────────────────────────────────
    mock_grpc = AsyncMock()
    mock_grpc.start = AsyncMock()
    mock_grpc.stop = AsyncMock()

    with (
        # Patch module-level vars so all import-site bindings work
        patch("app.core.database._client", mock_client),
        patch("app.core.redis_client._redis", mock_redis),
        # Patch lifecycle helpers at definition site
        patch("app.core.database.init_db", new_callable=AsyncMock),
        patch("app.core.database.close_db", new_callable=AsyncMock),
        patch("app.core.redis_client.init_redis", new_callable=AsyncMock),
        patch("app.core.redis_client.close_redis", new_callable=AsyncMock),
        # Patch at app.main import site (lifespan uses local name bindings)
        patch("app.main.init_db", new_callable=AsyncMock),
        patch("app.main.close_db", new_callable=AsyncMock),
        patch("app.main.init_redis", new_callable=AsyncMock),
        patch("app.main.close_redis", new_callable=AsyncMock),
        # Disable gRPC server in unit tests
        patch("app.main.GrpcServerLifecycle", return_value=mock_grpc),
    ):
        yield
