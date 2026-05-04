"""Unit tests — IdempotencyMiddleware + core/idempotency.py

검증:
  1. 헤더 없음 + opt-in 경로     → 통과 (201)
  2. 헤더 없음 + required 경로   → 400
  3. 잘못된 키 형식              → 400
  4. 정상 첫 요청                → 201 + Redis SET NX 1회 + 응답 저장 1회
  5. 같은 key + 같은 body 재요청 → 기존 응답 replay (Idempotent-Replay 헤더)
  6. 같은 key + 다른 body 재요청 → 422
  7. in_progress 레코드 재요청   → 409 + Retry-After
  8. 5xx 응답 시 슬롯 release   → 재시도 정상 진행
  9. Redis 장애 fail-open        → 핸들러 정상 실행
 10. 비-mutating (GET)           → 미들웨어 통과
"""
from __future__ import annotations

import os
import sys
import unittest.mock as _mock

import pytest

# 환경 기본값 (Settings 강제 필드)
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret")
os.environ.setdefault("GROQ_API_KEY", "test")
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/gcp.json")

# 외부 모듈 스텁
for _mod in [
    "motor", "motor.motor_asyncio",
    "pymongo", "pymongo.errors",
    "groq", "google.cloud", "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1", "grpc",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = _mock.MagicMock()

# redis / fakeredis 는 실제 패키지를 쓴다 — fakeredis 가 redis.Connection 을 서브클래싱.
# (mock 으로 덮어쓰면 metaclass conflict 발생)
fakeredis = pytest.importorskip("fakeredis")

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.core import idempotency as idem_mod
from app.core.idempotency import (
    AcquireResult,
    build_idem_key,
    compute_body_hash,
)
from app.middleware.idempotency_middleware import IdempotencyMiddleware


# ── Fixtures ──────────────────────────────────────────────────────────────────


@pytest.fixture
def fake_redis(monkeypatch):
    """fakeredis 로 실제 SETNX/GET 을 흉내."""
    r = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.core.idempotency.get_redis", lambda: r)
    return r


@pytest.fixture
def handler_calls():
    """핸들러가 몇 번 호출됐는지 추적."""
    return {"count": 0}


@pytest.fixture
def app(handler_calls):
    """최소 FastAPI 앱 — POST 만 가진 라우트 2개."""
    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    @app.post("/api/v1/scenarios/{sid}/publish")
    async def publish(sid: str, payload: dict):
        handler_calls["count"] += 1
        return JSONResponse(
            status_code=201,
            content={"id": sid, "version": payload.get("version", 1)},
        )

    @app.post("/api/v1/fail")
    async def fail():
        handler_calls["count"] += 1
        return JSONResponse(
            status_code=500,
            content={"error": "BOOM"},
        )

    @app.get("/api/v1/scenarios/{sid}")
    async def get_scn(sid: str):
        handler_calls["count"] += 1
        return {"id": sid}

    return app


@pytest.fixture
def client(app, fake_redis):
    return TestClient(app, raise_server_exceptions=False)


# ── 1. 헤더 없음 (opt-in) — 통과 ──────────────────────────────────────────────


def test_missing_header_passes_through(client, handler_calls):
    r = client.post(
        "/api/v1/scenarios/s1/publish",
        json={"version": 1},
    )
    assert r.status_code == 201
    assert handler_calls["count"] == 1


# ── 2. 헤더 없음 + required 경로 — 400 ────────────────────────────────────────


def test_missing_header_on_required_path_400(client, handler_calls, monkeypatch):
    monkeypatch.setattr(
        "app.core.config.settings.IDEMPOTENCY_REQUIRED_PATHS",
        "/api/v1/scenarios/",
    )
    r = client.post("/api/v1/scenarios/s1/publish", json={"v": 1})
    assert r.status_code == 400
    assert r.json()["error"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert handler_calls["count"] == 0


# ── 3. 잘못된 키 형식 ────────────────────────────────────────────────────────


@pytest.mark.parametrize("bad_key", [
    "short",          # 길이 < 8
    "a" * 129,        # 길이 > 128
    "has space",      # 공백
    "key!bad@chars",  # 허용문자(영숫자/-/_) 외 특수문자
])
def test_invalid_key_shape_400(client, handler_calls, bad_key):
    r = client.post(
        "/api/v1/scenarios/s1/publish",
        headers={"Idempotency-Key": bad_key},
        json={"version": 1},
    )
    assert r.status_code == 400
    assert r.json()["error"] == "IDEMPOTENCY_KEY_INVALID"
    assert handler_calls["count"] == 0


# ── 4. 정상 첫 요청 ──────────────────────────────────────────────────────────


def test_first_request_ok(client, handler_calls, fake_redis):
    r = client.post(
        "/api/v1/scenarios/s1/publish",
        headers={"Idempotency-Key": "abcd1234efgh"},
        json={"version": 1},
    )
    assert r.status_code == 201
    assert r.json() == {"id": "s1", "version": 1}
    assert handler_calls["count"] == 1
    # Idempotent-Replay 는 첫 요청에선 없어야 함
    assert "idempotent-replay" not in {h.lower() for h in r.headers}


# ── 5. 같은 key + 같은 body — replay ──────────────────────────────────────────


def test_same_key_same_body_replays_cached_response(client, handler_calls):
    headers = {"Idempotency-Key": "abcd1234efgh"}
    body = {"version": 2}

    r1 = client.post("/api/v1/scenarios/s1/publish", headers=headers, json=body)
    r2 = client.post("/api/v1/scenarios/s1/publish", headers=headers, json=body)

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r2.json() == r1.json()
    # 핸들러는 최초 1회만 실행
    assert handler_calls["count"] == 1
    assert r2.headers.get("idempotent-replay") == "true"


# ── 6. 같은 key + 다른 body — 422 ────────────────────────────────────────────


def test_same_key_different_body_422(client, handler_calls):
    headers = {"Idempotency-Key": "samekey00abcd"}
    r1 = client.post("/api/v1/scenarios/s1/publish", headers=headers, json={"version": 1})
    r2 = client.post("/api/v1/scenarios/s1/publish", headers=headers, json={"version": 9})
    assert r1.status_code == 201
    assert r2.status_code == 422
    assert r2.json()["error"] == "IDEMPOTENCY_KEY_MISMATCH"
    # 두 번째는 핸들러까지 못 감
    assert handler_calls["count"] == 1


# ── 7. in_progress 상태 — 409 ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_in_progress_returns_409(monkeypatch):
    """acquire_slot 을 직접 스텁해 in_progress 상황을 강제."""
    from app.middleware import idempotency_middleware as mm

    async def fake_acquire(*, key, body_hash):
        return AcquireResult(
            acquired=False,
            existing_status="in_progress",
            existing_body_hash=body_hash,
            cached=None,
        )

    monkeypatch.setattr(mm, "acquire_slot", fake_acquire)

    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    @app.post("/api/v1/scenarios/x/publish")
    async def h(payload: dict):
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/api/v1/scenarios/x/publish",
        headers={"Idempotency-Key": "inflight001"},
        json={"v": 1},
    )
    assert r.status_code == 409
    assert r.json()["error"] == "IDEMPOTENCY_IN_PROGRESS"
    assert r.headers.get("retry-after") == "5"


# ── 8. 5xx 응답 시 release — 재시도 가능 ─────────────────────────────────────


def test_5xx_releases_slot_allowing_retry(client, handler_calls, fake_redis):
    headers = {"Idempotency-Key": "fail0000abcd"}
    r1 = client.post("/api/v1/fail", headers=headers)
    assert r1.status_code == 500
    assert handler_calls["count"] == 1
    # 같은 키로 재시도가 가능해야 함 (슬롯 해제 됐으므로)
    r2 = client.post("/api/v1/fail", headers=headers)
    assert r2.status_code == 500
    assert handler_calls["count"] == 2


# ── 9. Redis 장애 시 fail-open ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_redis_failure_fails_open(monkeypatch):
    """redis.set 이 raise → 미들웨어는 일반 요청처럼 통과."""
    from app.middleware import idempotency_middleware as mm
    from app.core import idempotency as idem

    class _BrokenRedis:
        async def set(self, *a, **kw):
            raise idem.RedisError("redis down")

        async def get(self, *a, **kw):
            raise idem.RedisError("redis down")

        async def delete(self, *a, **kw):
            raise idem.RedisError("redis down")

    monkeypatch.setattr(
        "app.core.idempotency.get_redis",
        lambda: _BrokenRedis(),
    )

    app = FastAPI()
    app.add_middleware(IdempotencyMiddleware)

    calls = {"n": 0}

    @app.post("/api/v1/scenarios/y/publish")
    async def h(payload: dict):
        calls["n"] += 1
        return {"ok": True}

    client = TestClient(app, raise_server_exceptions=False)
    r = client.post(
        "/api/v1/scenarios/y/publish",
        headers={"Idempotency-Key": "redisdownkey1"},
        json={"v": 1},
    )
    # fail-open: 핸들러는 실행되고 200 응답
    assert r.status_code == 200
    assert calls["n"] == 1


# ── 10. GET 등 비-mutating 는 통과 ────────────────────────────────────────────


def test_get_bypasses_middleware(client, handler_calls):
    r = client.get(
        "/api/v1/scenarios/s1",
        headers={"Idempotency-Key": "abcd1234efgh"},
    )
    assert r.status_code == 200
    assert handler_calls["count"] == 1


# ── 순수 함수 헬퍼 ────────────────────────────────────────────────────────────


def test_build_idem_key_namespace():
    k = build_idem_key(
        tenant_id="tenantA",
        method="post",
        path="/api/v1/scenarios/x/publish",
        client_key="abcd1234efgh",
    )
    # tenant 스코프가 적용되고, 메서드는 upper
    assert ":POST:" in k
    assert "tenantA" in k
    assert k.endswith(":abcd1234efgh")


def test_compute_body_hash_stable_and_distinct():
    h1 = compute_body_hash(b'{"a":1}')
    h2 = compute_body_hash(b'{"a":1}')
    h3 = compute_body_hash(b'{"a":2}')
    assert h1 == h2
    assert h1 != h3
    # 빈 바디도 안정적
    assert compute_body_hash(b"") == compute_body_hash(b"")
