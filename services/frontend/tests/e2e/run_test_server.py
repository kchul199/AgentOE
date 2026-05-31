"""run_test_server.py — 테스트용 mock backend 서버 기동

외부 의존성(MongoDB, Redis, gRPC)을 모두 AsyncMock 으로 패치하고
실제 FastAPI app 을 uvicorn 으로 기동.
smoke 테스트 실행 전 subprocess 로 시작하고 완료 후 종료.

사용법:
    python3 run_test_server.py [--port 8000]
"""

import asyncio
import sys
import os

# backend 가 있는 곳을 PYTHONPATH 에 추가
BACKEND_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "../../../../backend")
)
sys.path.insert(0, BACKEND_DIR)

# ── 외부 패키지 sys.modules stub (pip install 없이 import 통과) ──────────
# app.core.* 가 motor/redis/grpc 를 import 하기 전에 주입해야 함
# try/except ImportError 블록 안의 import도 모두 커버해야 aioredis=None 방지
from unittest.mock import MagicMock as _MM

# Redis 예외를 실제 Exception 으로 만들어야 except RedisError 구문이 동작
class _FakeRedisError(Exception): pass
class _FakeConnectionError(_FakeRedisError): pass
class _FakeTimeoutError(_FakeRedisError): pass

def _make_stub(name: str) -> _MM:
    stub = _MM()
    stub.__name__ = name
    stub.__spec__ = None
    # Python import 시스템이 서브모듈 접근에 쓰는 속성
    stub.__path__ = []
    stub.__package__ = name
    return stub

# ① motor
_motor_asyncio = _make_stub("motor.motor_asyncio")
_motor_asyncio.AsyncIOMotorClient = _MM
_motor_asyncio.AsyncIOMotorDatabase = _MM
_motor = _make_stub("motor")
_motor.motor_asyncio = _motor_asyncio

# ② redis — redis.exceptions 에 실제 Exception 클래스를 넣어야
#            except RedisError 구문이 동작하고 aioredis=None 을 피함
_redis_exceptions = _make_stub("redis.exceptions")
_redis_exceptions.RedisError        = _FakeRedisError
_redis_exceptions.ConnectionError   = _FakeConnectionError
_redis_exceptions.TimeoutError      = _FakeTimeoutError
_redis_asyncio = _make_stub("redis.asyncio")
_redis_asyncio.Redis = _MM
_redis_asyncio.from_url = _MM()
_redis_client = _make_stub("redis.asyncio.client")
_redis = _make_stub("redis")
_redis.asyncio     = _redis_asyncio
_redis.exceptions  = _redis_exceptions
_redis.from_url    = _MM()

# ③ grpc
_grpc_aio = _make_stub("grpc.aio")
_grpc = _make_stub("grpc")
_grpc.aio = _grpc_aio

# ④ pymongo / bson
_pymongo_errors = _make_stub("pymongo.errors")
_pymongo = _make_stub("pymongo")
_pymongo.errors = _pymongo_errors
_bson_objectid = _make_stub("bson.objectid")
_bson = _make_stub("bson")
_bson.objectid = _bson_objectid

# ⑤ 기타 백엔드 의존성 — import 실패 시 추가
_structlog_contextvars = _make_stub("structlog.contextvars")
_structlog_contextvars.bind_contextvars = lambda **kw: None
_structlog_contextvars.clear_contextvars = lambda: None
_structlog_contextvars.merge_contextvars = lambda logger, method, event_dict: event_dict
_structlog = _make_stub("structlog")
_structlog.get_logger = lambda *a, **k: _MM()
_structlog.contextvars = _structlog_contextvars

_STUBS: dict = {
    "motor":                   _motor,
    "motor.motor_asyncio":     _motor_asyncio,
    "redis":                   _redis,
    "redis.asyncio":           _redis_asyncio,
    "redis.asyncio.client":    _redis_client,
    "redis.exceptions":        _redis_exceptions,
    "grpc":                    _grpc,
    "grpc.aio":                _grpc_aio,
    "pymongo":                 _pymongo,
    "pymongo.errors":          _pymongo_errors,
    "bson":                    _bson,
    "bson.objectid":           _bson_objectid,
    "structlog":               _structlog,
    "structlog.contextvars":   _structlog_contextvars,
}
for _mod_name, _stub_obj in _STUBS.items():
    if _mod_name not in sys.modules:
        sys.modules[_mod_name] = _stub_obj  # type: ignore

from unittest.mock import AsyncMock, MagicMock, patch

# ── 공통 패치 목록 ─────────────────────────────────────────────────────────
PATCHES = []


def _mock_session_repo():
    repo = MagicMock()
    repo.get_by_id = AsyncMock(return_value=None)
    repo.create = AsyncMock(return_value=MagicMock(id="test-session-id"))
    repo.update = AsyncMock(return_value=None)
    repo.delete = AsyncMock(return_value=None)
    repo.list = AsyncMock(return_value=[])
    return repo


def start_patches():
    mock_grpc = MagicMock()
    mock_grpc.return_value.start = AsyncMock()
    mock_grpc.return_value.stop = AsyncMock()

    _redis_mock = AsyncMock()
    _redis_mock.ping = AsyncMock(return_value=True)
    _redis_mock.get  = AsyncMock(return_value=None)
    _redis_mock.set  = AsyncMock(return_value=True)
    _redis_mock.incr = AsyncMock(return_value=1)
    _redis_mock.expire = AsyncMock(return_value=True)

    patches_spec = [
        # lifespan 바인딩 패치
        ("app.main.init_db",    AsyncMock()),
        ("app.main.close_db",   AsyncMock()),
        ("app.main.init_redis", AsyncMock()),
        ("app.main.close_redis",AsyncMock()),
        ("app.main.GrpcServerLifecycle", mock_grpc),
        ("app.main.SessionRepository",   MagicMock(return_value=_mock_session_repo())),
        # 미들웨어 의존성 — 이게 없으면 모든 요청 500
        ("app.middleware.rate_limit_middleware.rate_limit_check", AsyncMock(return_value=True)),
        ("app.domain.kill_switch.KillSwitchService.is_active",   AsyncMock(return_value=False)),
        ("app.core.redis_client.get_redis", MagicMock(return_value=_redis_mock)),
        ("app.core.database.get_database",  MagicMock(return_value=MagicMock())),
        # JWT 검증 — 모든 토큰 허용 (테스트 전용!)
        ("app.core.auth.verify_token", AsyncMock(
            return_value={"sub": "test-user", "tenant_id": "test-tenant"}
        )),
    ]
    for target, mock_obj in patches_spec:
        try:
            p = patch(target, new=mock_obj)
            p.start()
            PATCHES.append(p)
        except Exception:
            pass  # 없는 경로는 스킵


def stop_patches():
    for p in PATCHES:
        try:
            p.stop()
        except Exception:
            pass


if __name__ == "__main__":
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=18000)
    args = parser.parse_args()

    start_patches()
    try:
        import app.main  # noqa — 패치 후 임포트
        from app.main import app as fastapi_app

        print(f"[test-server] 기동 port={args.port}", flush=True)
        uvicorn.run(
            fastapi_app,
            host="127.0.0.1",
            port=args.port,
            log_level="warning",
        )
    finally:
        stop_patches()
