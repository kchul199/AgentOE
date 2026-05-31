"""
Integration (E2E) — JWKS mock + fakeredis 기반 인증/쿼터 경로 검증.

범위:
  1. JWKS 캐시 생명 주기
     - miss → fetch 성공 → 메트릭 `miss` 기록
     - hit  → 캐시에서 즉시 반환 → 메트릭 `hit`
     - kid 회전: 새 kid 토큰 검증 시 force_refresh 1회 발생
     - JWKS 엔드포인트 장애: stale 캐시 유지 (fail-open) + `failure` 히스토그램
     - 캐시 미존재 상태에서 fetch 실패: None 반환 + `fail` 메트릭
  2. Quota 경로 (fakeredis)
     - 쿼터 미설정 시 상시 ok
     - under/over 토큰 쿼터 + fallback 정책 → QuotaExceededError(graceful=True)
     - reject 정책 → graceful=False
     - commit_usage 가 실제 Redis 키를 INCRBY
     - Redis 장애(명시적 raise) 시 fail-open

이 테스트들은 외부 네트워크/진짜 Redis 없이 동작. 목적:
  - PR CI 에서 보안 크리티컬 경로가 배포 직전에 규제된 시나리오로 항상 검증되도록 하는 것.
  - unit test 로는 드러나지 않는 **상호작용 regression** (예: httpx 버전 변경 시 JWKS
    파싱 깨짐) 을 잡는다.
"""

from __future__ import annotations

import os
import sys
import unittest.mock as _mock
from typing import Any

import pytest

# ── 테스트용 환경 변수 기본값 (app.core.config.Settings 가 required 로 강제) ──
# 실제 값은 사용되지 않음. Settings 가 import 시점에 생성되므로 여기서 주입.
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-jwt-secret-not-real")
os.environ.setdefault("GROQ_API_KEY", "test-groq-key")
os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", "/tmp/gcp.json")

# 테스트 실행 환경에 SOCKS 프록시 env 가 설정되어 있을 수 있음 → httpx 가
# 내부 localhost mock 서버를 프록시로 보내려다 실패. 통합 테스트에서는 제거.
for _proxy_var in (
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "ALL_PROXY",
    "http_proxy",
    "https_proxy",
    "all_proxy",
):
    os.environ.pop(_proxy_var, None)

# ── 외부 모듈 모킹 (integration test 모두 공통 패턴) ─────────────────────────
# test_metrics_api 와 동일한 트릭 — 실제 motor/groq 등이 설치되어 있지 않은
# 환경에서도 import 가 성공해야 통합 경로가 실행된다.
for _mod in [
    "motor",
    "motor.motor_asyncio",
    "pymongo",
    "pymongo.errors",
    "groq",
    "google.cloud",
    "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1",
    "grpc",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = _mock.MagicMock()

pytest_httpserver = pytest.importorskip("pytest_httpserver")
fakeredis = pytest.importorskip("fakeredis")
jose = pytest.importorskip("jose")
crypto_rsa = pytest.importorskip("cryptography.hazmat.primitives.asymmetric.rsa")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwt
from jose.utils import long_to_base64

# ── 대상 모듈 ────────────────────────────────────────────────────────────────
from app.core import jwks_cache as jwks_mod
from app.core import metrics as m
from app.core import quota as quota_mod
from app.core import redis_client as rc

pytestmark = pytest.mark.integration


# ── Helpers: RSA 키 + JWK 변환 ───────────────────────────────────────────────


def _generate_rsa_keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _private_key_pem(key: rsa.RSAPrivateKey) -> bytes:
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )


def _public_jwk(key: rsa.RSAPrivateKey, kid: str) -> dict[str, Any]:
    """RSA 공개키를 JWK(RS256) 로 직렬화."""
    pub = key.public_key().public_numbers()
    return {
        "kty": "RSA",
        "kid": kid,
        "alg": "RS256",
        "use": "sig",
        "n": long_to_base64(pub.n).decode(),
        "e": long_to_base64(pub.e).decode(),
    }


def _make_jwks_payload(*keys: tuple[rsa.RSAPrivateKey, str]) -> dict[str, Any]:
    return {"keys": [_public_jwk(k, kid) for k, kid in keys]}


def _sign_token(
    key: rsa.RSAPrivateKey,
    kid: str,
    *,
    tenant_id: str = "t_acme",
    sub: str = "user-1",
) -> str:
    return jwt.encode(
        {"tenant_id": tenant_id, "sub": sub},
        _private_key_pem(key),
        algorithm="RS256",
        headers={"kid": kid},
    )


# ── Fixtures ─────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_metrics_store() -> None:
    """각 테스트 간 메트릭 격리."""
    m._store = m._MetricsStore()


@pytest.fixture
def fresh_jwks_cache(monkeypatch):
    """모듈 싱글턴을 새 인스턴스로 바꿔 테스트 간 격리."""
    cache = jwks_mod.JWKSCache()
    monkeypatch.setattr(jwks_mod, "jwks_cache", cache)
    return cache


@pytest.fixture
def configure_jwks(monkeypatch, httpserver):
    """settings.JWKS_URL 을 httpserver 주소로 교체 + 캐시 TTL 단축."""
    url = httpserver.url_for("/jwks.json")
    monkeypatch.setattr(jwks_mod.settings, "JWKS_URL", url)
    # 명시적으로 설정값 존재를 보장 — pydantic Settings 는 getattr 스타일 접근
    monkeypatch.setattr(jwks_mod.settings, "JWKS_CACHE_TTL_SECONDS", 60)
    return url


@pytest.fixture
async def fake_redis(monkeypatch):
    """fakeredis.aioredis 로 app.core.redis_client._redis 를 교체."""
    client = fakeredis.aioredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr(rc, "_redis", client)
    yield client
    await client.aclose()


# ── JWKS cache lifecycle ─────────────────────────────────────────────────────


async def test_jwks_miss_then_hit(fresh_jwks_cache, configure_jwks, httpserver):
    """첫 조회는 miss(fetch 수행) → 두 번째는 hit."""
    kp, kid = _generate_rsa_keypair(), "k1"
    httpserver.expect_request("/jwks.json").respond_with_json(
        _make_jwks_payload((kp, kid)),
    )

    jwk1 = await fresh_jwks_cache.get_key(kid)
    jwk2 = await fresh_jwks_cache.get_key(kid)

    assert jwk1 is not None and jwk1["kid"] == kid
    assert jwk2 is not None and jwk2["kid"] == kid
    # 메트릭: miss 1회, hit 1회
    assert m._store.jwks_lookups["miss"].get() == 1.0
    assert m._store.jwks_lookups["hit"].get() == 1.0
    # refresh 히스토그램: success 카운트 1 이상 (첫 fetch)
    assert m._store.jwks_refresh_duration_s["success"].stats()["count"] >= 1


async def test_jwks_kid_rotation_triggers_force_refresh(
    fresh_jwks_cache,
    configure_jwks,
    httpserver,
):
    """
    처음엔 k1 만 발급 → k1 캐시 → 이후 IdP 가 k2 추가.
    새 kid 를 가진 토큰 검증 시 force_refresh 1회 발생, 그 뒤엔 k2 도 hit.
    """
    kp1, kp2 = _generate_rsa_keypair(), _generate_rsa_keypair()

    # 1) 초기 JWKS 응답에는 k1 만
    httpserver.expect_oneshot_request("/jwks.json").respond_with_json(
        _make_jwks_payload((kp1, "k1")),
    )
    jwk1 = await fresh_jwks_cache.get_key("k1")
    assert jwk1 is not None

    # 2) 회전 후: JWKS 에 k1 + k2 둘 다 존재
    httpserver.expect_request("/jwks.json").respond_with_json(
        _make_jwks_payload((kp1, "k1"), (kp2, "k2")),
    )

    # 3) k2 로 조회 → 캐시에 없으므로 force_refresh, 성공적으로 반환
    jwk2 = await fresh_jwks_cache.get_key("k2")
    assert jwk2 is not None and jwk2["kid"] == "k2"

    # 메트릭: force_refresh 1회
    assert m._store.jwks_lookups["force_refresh"].get() == 1.0


async def test_jwks_endpoint_failure_keeps_stale_cache(
    fresh_jwks_cache,
    configure_jwks,
    httpserver,
):
    """
    캐시에 유효 값이 있는 상태에서 원격이 장애 → stale 반환 (fail-open).
    refresh histogram 에는 `failure` 샘플이 남는다.
    """
    kp, kid = _generate_rsa_keypair(), "k1"

    # 정상 응답 1번 (초기 캐싱)
    httpserver.expect_oneshot_request("/jwks.json").respond_with_json(
        _make_jwks_payload((kp, kid)),
    )
    assert await fresh_jwks_cache.get_key(kid) is not None

    # 이후 장애 응답 + kid 미스를 유도해 force-refresh 를 시키고 실패시킴
    httpserver.expect_request("/jwks.json").respond_with_data("boom", status=500)

    # 존재하지 않는 kid 조회 → force_refresh → fetch 500 → 기존 stale 캐시 기준으로 None
    result = await fresh_jwks_cache.get_key("unknown_kid")
    assert result is None

    failure_count = m._store.jwks_refresh_duration_s["failure"].stats()["count"]
    assert failure_count >= 1


async def test_jwks_when_no_cache_and_fetch_fails_returns_none(
    fresh_jwks_cache,
    configure_jwks,
    httpserver,
):
    """캐시 전무 + fetch 실패 = None 반환 + `fail` 메트릭 기록."""
    httpserver.expect_request("/jwks.json").respond_with_data("oops", status=503)

    result = await fresh_jwks_cache.get_key("any")
    assert result is None
    assert m._store.jwks_lookups["fail"].get() >= 1.0


# ── Quota with fakeredis ─────────────────────────────────────────────────────


async def test_quota_under_limit_ok(fake_redis, monkeypatch):
    """카운터 0 → ok 경로."""
    monkeypatch.setattr(quota_mod.settings, "LLM_QUOTA_ENABLED", True)
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_DAILY_TOKEN_QUOTA_DEFAULT",
        1_000_000,
    )
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_DAILY_COST_QUOTA_CENTS_DEFAULT",
        10_000,
    )

    status = await quota_mod.enforce_quota("t_acme")
    assert status.over is False
    assert m._store.llm_quota_checks["t_acme:none:ok"].get() == 1.0


async def test_quota_over_tokens_fallback(fake_redis, monkeypatch):
    """토큰 한도 초과 + fallback 정책 → graceful=True."""
    monkeypatch.setattr(quota_mod.settings, "LLM_QUOTA_ENABLED", True)
    monkeypatch.setattr(quota_mod.settings, "LLM_DAILY_TOKEN_QUOTA_DEFAULT", 100)
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_DAILY_COST_QUOTA_CENTS_DEFAULT",
        0,  # 무제한
    )
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_QUOTA_EXCEEDED_BEHAVIOR",
        "fallback",
    )

    # 사용량 초과를 만들어둠
    await quota_mod.commit_usage("t_acme", tokens=150)

    with pytest.raises(quota_mod.QuotaExceededError) as exc_info:
        await quota_mod.enforce_quota("t_acme")
    assert exc_info.value.graceful is True
    assert exc_info.value.scope == "tokens"
    assert m._store.llm_quota_checks["t_acme:tokens:fallback"].get() == 1.0


async def test_quota_over_cost_reject(fake_redis, monkeypatch):
    """비용 한도 초과 + reject 정책 → graceful=False."""
    monkeypatch.setattr(quota_mod.settings, "LLM_QUOTA_ENABLED", True)
    monkeypatch.setattr(quota_mod.settings, "LLM_DAILY_TOKEN_QUOTA_DEFAULT", 0)
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_DAILY_COST_QUOTA_CENTS_DEFAULT",
        500,
    )
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_QUOTA_EXCEEDED_BEHAVIOR",
        "reject",
    )

    await quota_mod.commit_usage("t_acme", tokens=0, cost_cents=900.0)

    with pytest.raises(quota_mod.QuotaExceededError) as exc_info:
        await quota_mod.enforce_quota("t_acme")
    assert exc_info.value.graceful is False
    assert exc_info.value.scope == "cost"
    assert m._store.llm_quota_checks["t_acme:cost:reject"].get() == 1.0


async def test_quota_warn_policy_passes_through(fake_redis, monkeypatch):
    """warn 정책은 예외 대신 status 반환 + warn 메트릭."""
    monkeypatch.setattr(quota_mod.settings, "LLM_QUOTA_ENABLED", True)
    monkeypatch.setattr(quota_mod.settings, "LLM_DAILY_TOKEN_QUOTA_DEFAULT", 50)
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_DAILY_COST_QUOTA_CENTS_DEFAULT",
        0,
    )
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_QUOTA_EXCEEDED_BEHAVIOR",
        "warn",
    )
    await quota_mod.commit_usage("t_acme", tokens=100)

    status = await quota_mod.enforce_quota("t_acme")
    assert status.over_tokens is True
    assert m._store.llm_quota_checks["t_acme:tokens:warn"].get() == 1.0


async def test_commit_usage_increments_redis(fake_redis, monkeypatch):
    """commit_usage 가 실제 Redis 키를 정확히 INCRBY 하는지."""
    monkeypatch.setattr(quota_mod.settings, "LLM_QUOTA_ENABLED", True)
    await quota_mod.commit_usage("t_acme", tokens=42, cost_cents=7.0)

    tkey = quota_mod._tokens_key("t_acme")
    ckey = quota_mod._cost_key("t_acme")
    assert int(await fake_redis.get(tkey)) == 42
    assert int(await fake_redis.get(ckey)) == 7


async def test_quota_redis_failure_fail_open(monkeypatch):
    """
    Redis 가 예외를 던져도 enforce_quota 는 raise 하지 않고 통과해야 함
    (CLAUDE.md 원칙: 통화가 끊기지 않는 것이 최우선).
    """
    monkeypatch.setattr(quota_mod.settings, "LLM_QUOTA_ENABLED", True)
    monkeypatch.setattr(
        quota_mod.settings,
        "LLM_DAILY_TOKEN_QUOTA_DEFAULT",
        100,
    )

    class _Exploding:
        async def mget(self, *_a, **_kw):
            raise quota_mod.RedisError("redis down")

    monkeypatch.setattr(quota_mod, "get_redis", lambda: _Exploding())

    status = await quota_mod.enforce_quota("t_acme")
    # fail-open: 사용량 0 으로 간주 → not over
    assert status.over is False
    assert m._store.llm_quota_checks["t_acme:none:ok"].get() == 1.0
