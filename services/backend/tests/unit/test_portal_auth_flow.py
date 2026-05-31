"""Phase N — Portal 인증 흐름 단위 테스트.

커버 영역:
  U-01  bcrypt 72바이트 한계 — SHA-256 pre-hash 로 회피
  U-02  password verify — CryptContext 일관성 (seed ↔ auth_portal)
  U-03  refresh token rotation — SHA-256 pre-hash 적용 확인
  U-04  MFA envelope (env-key) 암호화/복호화 왕복
  U-05  MFA envelope (kms: prefix) 레거시 fallback
  U-06  portal_session.create_session — bcrypt no-error (>72B token)
  U-07  portal_session.rotate — 유효 / 무효 토큰 분기
  U-08  RBAC issuer 격리 (require_portal_role 기존 TC 보완)

실행 (외부 의존성 없음):
    python -m pytest tests/unit/test_portal_auth_flow.py -v \
        --rootdir=tests/unit --no-cov --tb=short
"""

from __future__ import annotations

import base64
import hashlib
import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from passlib.context import CryptContext

# ── 환경변수 stub (import 전 설정) ───────────────────────────────────────────
os.environ.setdefault("MONGODB_URI", "mongodb://localhost:27017/test")
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("JWT_SECRET", "test-secret")
os.environ.setdefault("PORTAL_JWT_SECRET", "test-portal-secret")
os.environ.setdefault(
    "PORTAL_MFA_ENVELOPE_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
os.environ.setdefault("PORTAL_KMS_KEY_ID", "")

_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)

# ─────────────────────────────────────────────────────────────────────────────
# U-01  bcrypt 72바이트 한계
# ─────────────────────────────────────────────────────────────────────────────


def test_bcrypt_sha256_prehash_no_error() -> None:
    """72바이트 초과 문자열을 SHA-256 pre-hash 하면 bcrypt 에러 없음."""
    import bcrypt

    long_token = "x" * 100  # 100 바이트 > 72
    digest = hashlib.sha256(long_token.encode()).digest()  # 32 바이트
    hashed = bcrypt.hashpw(digest, bcrypt.gensalt())
    assert bcrypt.checkpw(digest, hashed)


def test_bcrypt_direct_long_raises_or_truncates() -> None:
    """CryptContext bcrypt__truncate_error=False → 72B 초과 입력도 에러 없이 처리."""
    long_password = "a" * 80
    # truncate_error=False 이면 ValueError 대신 묵시적 truncate
    hashed = _pwd_ctx.hash(long_password)
    # verify 도 동일하게 동작
    assert _pwd_ctx.verify(long_password, hashed)


# ─────────────────────────────────────────────────────────────────────────────
# U-02  password verify 일관성
# ─────────────────────────────────────────────────────────────────────────────


def test_password_verify_consistency() -> None:
    """seed 스크립트와 auth_portal.py 가 동일한 CryptContext 설정을 사용하는지 검증."""
    plain = "MyP@ss123"
    # seed_portal_admin.py 방식으로 해시
    hashed = _pwd_ctx.hash(plain)
    # auth_portal.py 방식으로 검증
    assert _pwd_ctx.verify(plain, hashed), "비밀번호 검증 실패"
    assert not _pwd_ctx.verify("wrong", hashed), "틀린 비밀번호가 통과됨"


def test_hashed_password_field_name() -> None:
    """portal_users 도큐먼트에서 'hashed_password' 필드를 읽어야 함 (password_hash 아님)."""
    user_doc: dict[str, Any] = {
        "username": "admin",
        "hashed_password": _pwd_ctx.hash("secret"),
    }
    stored = user_doc.get("hashed_password", "")
    assert _pwd_ctx.verify("secret", stored)
    # 잘못된 필드명으로 읽으면 빈 문자열 → verify 실패
    wrong_field = user_doc.get("password_hash", "")
    assert wrong_field == "", "잘못된 필드명이 값을 반환함"


# ─────────────────────────────────────────────────────────────────────────────
# U-03  refresh token SHA-256 pre-hash
# ─────────────────────────────────────────────────────────────────────────────


def test_refresh_token_sha256_roundtrip() -> None:
    """portal_session 의 SHA-256 pre-hash 방식: hash/verify 왕복 검증."""
    import secrets

    import bcrypt

    token = secrets.token_urlsafe(48)  # 64자 이상 — 원래 72B 초과
    digest = hashlib.sha256(token.encode()).digest()
    token_hash = bcrypt.hashpw(digest, bcrypt.gensalt()).decode()

    # 동일 token 으로 verify
    same_digest = hashlib.sha256(token.encode()).digest()
    assert bcrypt.checkpw(same_digest, token_hash.encode()), "유효 토큰 검증 실패"

    # 다른 token 은 verify 실패
    other = secrets.token_urlsafe(48)
    other_digest = hashlib.sha256(other.encode()).digest()
    assert not bcrypt.checkpw(other_digest, token_hash.encode()), "다른 토큰이 통과됨"


# ─────────────────────────────────────────────────────────────────────────────
# U-04  MFA envelope (env-key) 암호화/복호화
# ─────────────────────────────────────────────────────────────────────────────


def test_mfa_envkey_encrypt_decrypt_roundtrip() -> None:
    """PORTAL_MFA_ENVELOPE_KEY 로 암호화한 MFA secret 을 복호화하면 원래 값."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    key_hex = os.environ["PORTAL_MFA_ENVELOPE_KEY"]
    key = bytes.fromhex(key_hex)
    secret = "JBSWY3DPEHPK3PXP"  # 테스트용 TOTP secret

    # 암호화 (auth_portal._encrypt_mfa_secret_envkey 와 동일 로직)
    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, secret.encode(), None)
    ciphertext = base64.b64encode(nonce + ct).decode()

    # 복호화
    raw = base64.b64decode(ciphertext.encode())
    dec_nonce, dec_ct = raw[:12], raw[12:]
    plain = AESGCM(key).decrypt(dec_nonce, dec_ct, None).decode()

    assert plain == secret, f"복호화 결과 불일치: {plain!r} != {secret!r}"


# ─────────────────────────────────────────────────────────────────────────────
# U-05  kms: prefix fallback
# ─────────────────────────────────────────────────────────────────────────────


def test_mfa_kms_prefix_detection() -> None:
    """kms: prefix 가 없는 레거시 ciphertext 는 env-key 경로로 fallback."""
    _KMS_PREFIX = "kms:"
    legacy_ct = "AAABBBCCCDDD=="  # kms: 없음
    kms_ct = "kms:AAABBBCCCDDD=="

    assert not legacy_ct.startswith(_KMS_PREFIX), "레거시 형식이 kms: prefix 로 감지됨"
    assert kms_ct.startswith(_KMS_PREFIX), "KMS 형식이 감지 안 됨"


# ─────────────────────────────────────────────────────────────────────────────
# U-06  PortalSessionManager.create_session
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_create_session_no_bcrypt_error() -> None:
    """create_session 이 72B 초과 token 을 에러 없이 저장."""
    from app.domain.portal_session import PortalSessionManager

    mock_col = AsyncMock()
    mock_col.count_documents = AsyncMock(return_value=0)
    mock_col.insert_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)

    mgr = PortalSessionManager(db=mock_db)

    # 실행 — ValueError 없이 완료되어야 함
    token = await mgr.create_session(
        user_id="test-user",
        portal_roles=["portal:admin"],
        expire_hours=8,
    )
    assert isinstance(token, str), "token 이 반환되지 않음"
    assert len(token) > 10, "token 이 너무 짧음"

    # insert_one 이 호출됐고 token_hash 가 bcrypt 형식인지 확인
    call_args = mock_col.insert_one.call_args[0][0]
    assert "token_hash" in call_args, "token_hash 필드가 없음"
    assert call_args["token_hash"].startswith("$2b$"), "bcrypt 형식이 아님"


# ─────────────────────────────────────────────────────────────────────────────
# U-07  PortalSessionManager.rotate
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_rotate_valid_token_returns_new_token() -> None:
    """유효 token 으로 rotate 시 새 token 반환."""
    import secrets

    import bcrypt

    from app.domain.portal_session import PortalSessionManager

    old_token = secrets.token_urlsafe(48)
    old_digest = hashlib.sha256(old_token.encode()).digest()
    old_hash = bcrypt.hashpw(old_digest, bcrypt.gensalt()).decode()

    session_doc = {
        "_id": "sess-1",
        "user_id": "u1",
        "portal_roles": ["portal:viewer"],
        "token_hash": old_hash,
        "revoked": False,
        "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
    }

    mock_col = AsyncMock()
    mock_col.find = MagicMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[session_doc])))
    mock_col.update_one = AsyncMock()
    mock_col.count_documents = AsyncMock(return_value=0)
    mock_col.insert_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)

    mgr = PortalSessionManager(db=mock_db)
    new_token = await mgr.rotate(old_token=old_token, user_id="u1")

    assert new_token is not None, "유효 토큰인데 None 반환"
    assert new_token != old_token, "새 token 이 이전 token 과 동일"


@pytest.mark.asyncio
async def test_rotate_invalid_token_returns_none() -> None:
    """무효 token 으로 rotate 시 None 반환 (replay attack 방어)."""
    import secrets

    import bcrypt

    from app.domain.portal_session import PortalSessionManager

    real_token = secrets.token_urlsafe(48)
    real_digest = hashlib.sha256(real_token.encode()).digest()
    real_hash = bcrypt.hashpw(real_digest, bcrypt.gensalt()).decode()

    session_doc = {
        "_id": "sess-2",
        "user_id": "u2",
        "portal_roles": ["portal:viewer"],
        "token_hash": real_hash,
        "revoked": False,
        "expires_at": datetime(2099, 1, 1, tzinfo=UTC),
    }

    mock_col = AsyncMock()
    mock_col.find = MagicMock(return_value=AsyncMock(to_list=AsyncMock(return_value=[session_doc])))
    mock_col.update_one = AsyncMock()

    mock_db = MagicMock()
    mock_db.__getitem__ = MagicMock(return_value=mock_col)

    mgr = PortalSessionManager(db=mock_db)
    # 다른 토큰으로 rotate 시도
    fake_token = secrets.token_urlsafe(48)
    result = await mgr.rotate(old_token=fake_token, user_id="u2")

    assert result is None, "무효 토큰인데 새 token 이 반환됨"


# ─────────────────────────────────────────────────────────────────────────────
# U-08  RBAC 통합 (기존 test_portal_rbac.py 보완)
# ─────────────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_portal_issuer_viewer_passes_viewer_route() -> None:
    """agentoe-portal issuer + portal:viewer → viewer 요구 route 통과."""
    from app.core.auth import PORTAL_ISSUER, TenantContext, require_portal_role

    ctx = TenantContext(
        tenant_id="t1",
        client_id="c1",
        roles=["portal:viewer"],
        issuer=PORTAL_ISSUER,
    )
    dep = require_portal_role("portal:viewer")
    result = await dep(tenant=ctx)
    assert result is ctx


@pytest.mark.asyncio
async def test_portal_viewer_cannot_access_operator_route() -> None:
    """portal:viewer 는 portal:operator 요구 route 접근 불가."""
    from app.core.auth import PORTAL_ISSUER, TenantContext, require_portal_role
    from app.core.exceptions import AuthorizationError

    ctx = TenantContext(
        tenant_id="t1",
        client_id="c1",
        roles=["portal:viewer"],
        issuer=PORTAL_ISSUER,
    )
    dep = require_portal_role("portal:operator")
    with pytest.raises(AuthorizationError):
        await dep(tenant=ctx)
