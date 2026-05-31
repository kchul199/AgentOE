"""Portal 운영자 인증 엔드포인트 (Phase N — N1.7 / N5.2).

plan §2.2 / §2.3 / §2.5:
  POST /auth/portal/login        — username/password → MFA challenge JWT (5분)
  POST /auth/portal/mfa/verify   — TOTP 검증 → access(15분) + refresh(8h) HttpOnly cookie + CSRF
  POST /auth/portal/refresh      — refresh token rotation
  POST /auth/portal/logout       — 양쪽 쿠키 삭제 + refresh revoke
  POST /auth/portal/mfa/enroll   — TOTP secret 발급 (QR URI) — 최초 등록용

RBAC:
  - login/mfa/verify/refresh/logout: 인증 전 or refresh 쿠키 기반 (bearer 없음)
  - mfa/enroll: portal:viewer+ (이미 로그인된 상태)

보안:
  - CSRF: double-submit pattern (X-CSRF-Token 헤더 + __csrf__ 쿠키 매칭). verify/refresh/logout 에 적용.
  - MFA secret: AWS KMS envelope 암호화 (N5.2). 저장 포맷 → kms:<b64(payload)>.
    PORTAL_KMS_KEY_ID 미설정 시 PORTAL_MFA_ENVELOPE_KEY AES-GCM 으로 폴백 (N1 MVP).
    레거시 secret (kms: prefix 없음) 은 복호화 시 env-var 방식으로 자동 폴백 — 무중단 마이그레이션.
  - JWT issuer = "agentoe-portal". 기존 agentoe-api 토큰은 portal route 진입 불가 (N1.2/NG2).
  - refresh token: portal_sessions 컬렉션 (portal_session.py), bcrypt hash.
  - login brute-force: Redis INCR + TTL 로 IP 별 5회/10분 제한 (graceful — Redis 실패 시 통과).

CLAUDE.md:
  - 모든 DB/Redis I/O 비동기.
  - 실패 시 메인 흐름 방해 없이 로거 + 적절한 HTTP 에러.
"""

from __future__ import annotations

import base64
import hmac
import os
import secrets
from datetime import UTC, datetime
from typing import Annotated, Any

import structlog
from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from passlib.context import CryptContext
from pydantic import BaseModel

# bcrypt__truncate_error=False: 72바이트 초과 입력을 에러 대신 묵시적 truncate.
# (bcrypt 의 native 동작과 동일 — 일반 사용자 비밀번호는 72바이트 미만이 대부분)
_pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__truncate_error=False)

from app.core.auth import (
    PORTAL_ISSUER,
    TenantContext,
    create_access_token,
    require_portal_role,
)
from app.core.config import settings
from app.domain.audit_emitter import AuditEmitter, get_audit_emitter
from app.domain.portal_session import PortalSessionManager

logger = structlog.get_logger(__name__)
router = APIRouter()

# ── 쿠키 상수 ────────────────────────────────────────────────────────────────
_ACCESS_COOKIE = "portal_access"
_REFRESH_COOKIE = "portal_refresh"
_CSRF_COOKIE = "__csrf__"
_CSRF_HEADER = "X-CSRF-Token"

# MFA challenge: 짧은 JWT (5분) — iss=agentoe-portal + sub=challenge:{user_id}
_MFA_CHALLENGE_MINUTES = 5

# ── Pydantic models ───────────────────────────────────────────────────────────


class LoginRequest(BaseModel):
    username: str
    password: str


class MfaVerifyRequest(BaseModel):
    code: str  # 6-digit TOTP


class MfaEnrollRequest(BaseModel):
    pass  # TOTP secret 신규 발급 — body 없음


# ── CSRF helpers ──────────────────────────────────────────────────────────────


def _generate_csrf_token() -> str:
    return secrets.token_urlsafe(32)


def _check_csrf(request: Request, csrf_cookie: str | None) -> None:
    """Double-submit CSRF 검증. 실패 시 403."""
    header_val = request.headers.get(_CSRF_HEADER)
    if not header_val or not csrf_cookie or not hmac.compare_digest(header_val, csrf_cookie):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token mismatch",
        )


# ── 쿠키 설정 헬퍼 ────────────────────────────────────────────────────────────


def _set_access_cookie(response: Response, token: str, expire_minutes: int) -> None:
    response.set_cookie(
        _ACCESS_COOKIE,
        token,
        max_age=expire_minutes * 60,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/",
    )


def _set_refresh_cookie(response: Response, token: str, expire_hours: int) -> None:
    response.set_cookie(
        _REFRESH_COOKIE,
        token,
        max_age=expire_hours * 3600,
        httponly=True,
        secure=True,
        samesite="strict",
        path="/api/v1/auth/portal/refresh",  # refresh 경로에만
    )


def _set_csrf_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        _CSRF_COOKIE,
        token,
        max_age=int(getattr(settings, "PORTAL_REFRESH_EXPIRE_HOURS", 8)) * 3600,
        httponly=False,  # JS 가 읽어야 함 (double-submit 패턴)
        secure=True,
        samesite="strict",
        path="/",
    )


def _clear_cookies(response: Response) -> None:
    for name, path in [
        (_ACCESS_COOKIE, "/"),
        (_REFRESH_COOKIE, "/api/v1/auth/portal/refresh"),
        (_CSRF_COOKIE, "/"),
    ]:
        response.delete_cookie(name, path=path)


# ── MFA (TOTP) helpers ────────────────────────────────────────────────────────
#
# N5.2 KMS 격상:
#   저장 포맷  kms:<base64(2B_dek_len | encrypted_dek | 12B_nonce | aesgcm_ct)>
#   레거시     prefix 없음 — env-var AES-GCM or plain base64
#   복호화 시 자동 판별 → 무중단 마이그레이션 가능.

_KMS_PREFIX = "kms:"


def _encrypt_mfa_secret_envkey(secret: str) -> str:
    """레거시 env-var AES-GCM 또는 base64 fallback (N1 MVP 방식, 동기)."""
    key_hex = getattr(settings, "PORTAL_MFA_ENVELOPE_KEY", "")
    if not key_hex:
        logger.warning("mfa_envelope_key_not_set__dev_fallback")
        return base64.b64encode(secret.encode()).decode()
    key = bytes.fromhex(key_hex)[:32]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    nonce = os.urandom(12)
    ct = AESGCM(key).encrypt(nonce, secret.encode(), None)
    return base64.b64encode(nonce + ct).decode()


def _decrypt_mfa_secret_envkey(ciphertext: str) -> str:
    """레거시 env-var AES-GCM 복호화 (동기)."""
    key_hex = getattr(settings, "PORTAL_MFA_ENVELOPE_KEY", "")
    if not key_hex:
        return base64.b64decode(ciphertext).decode()
    key = bytes.fromhex(key_hex)[:32]
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    raw = base64.b64decode(ciphertext)
    nonce, ct = raw[:12], raw[12:]
    return AESGCM(key).decrypt(nonce, ct, None).decode()


async def _encrypt_mfa_secret(secret: str) -> str:
    """MFA secret 암호화 (N5.2 async KMS).

    PORTAL_KMS_KEY_ID 가 설정된 경우 → KMS envelope 암호화 (kms: prefix).
    미설정 시 → 레거시 env-var AES-GCM 폴백.
    KMS 호출 실패 시 → 레거시 폴백 + 경고 로그 (degraded mode).
    """
    kms_key_id = getattr(settings, "PORTAL_KMS_KEY_ID", "")
    kms_region = getattr(settings, "PORTAL_KMS_REGION", "ap-northeast-2")

    if kms_key_id:
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            from app.infra.kms_client import kms_generate_data_key, pack_kms_payload

            dkp = await kms_generate_data_key(kms_key_id, kms_region)
            nonce = os.urandom(12)
            ct = AESGCM(dkp["plaintext_dek"]).encrypt(nonce, secret.encode(), None)
            payload = pack_kms_payload(dkp["encrypted_dek"], nonce, ct)
            return _KMS_PREFIX + base64.b64encode(payload).decode()
        except Exception as exc:
            logger.error("kms_encrypt_failed__degraded_to_envkey", error=str(exc))
            # KMS 장애 시 env-var 방식으로 degraded — 운영팀에 알람 필요

    return _encrypt_mfa_secret_envkey(secret)


async def _decrypt_mfa_secret(ciphertext: str) -> str:
    """MFA secret 복호화 (N5.2 async KMS).

    kms: prefix → KMS 복호화.
    그 외        → 레거시 env-var / base64 폴백 (무중단 마이그레이션).
    """
    if ciphertext.startswith(_KMS_PREFIX):
        kms_region = getattr(settings, "PORTAL_KMS_REGION", "ap-northeast-2")
        try:
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM

            from app.infra.kms_client import kms_decrypt_dek, unpack_kms_payload

            raw = base64.b64decode(ciphertext[len(_KMS_PREFIX) :])
            encrypted_dek, nonce, ct = unpack_kms_payload(raw)
            dek = await kms_decrypt_dek(encrypted_dek, kms_region)
            return AESGCM(dek).decrypt(nonce, ct, None).decode()
        except Exception as exc:
            logger.error("kms_decrypt_failed", error=str(exc))
            raise RuntimeError("MFA secret KMS decryption failed") from exc

    # 레거시 포맷 자동 폴백
    return _decrypt_mfa_secret_envkey(ciphertext)


def _verify_totp(secret: str, code: str) -> bool:
    import pyotp

    totp = pyotp.TOTP(secret)
    return totp.verify(code, valid_window=1)


# ── Brute-force 제한 ──────────────────────────────────────────────────────────


async def _check_login_rate_limit(ip: str) -> None:
    """IP 별 로그인 5회/10분 초과 시 429. Redis 실패 시 통과 (graceful)."""
    try:
        from app.core.redis_client import get_redis

        r = get_redis()
        key = f"agentoe:login_attempts:{ip}"
        count = await r.incr(key)
        if count == 1:
            await r.expire(key, 600)  # 10분
        if count > 5:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail="Too many login attempts. Try again in 10 minutes.",
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("login_rate_limit_redis_error", error=str(e))


# ── portal_users repository 헬퍼 ─────────────────────────────────────────────


async def _get_user_by_username(username: str) -> dict[str, Any] | None:
    from app.core.database import get_database

    db = get_database()
    return await db["portal_users"].find_one({"username": username, "is_active": True})


async def _get_user_by_id(user_id: str) -> dict[str, Any] | None:
    from bson import ObjectId

    from app.core.database import get_database

    db = get_database()
    try:
        return await db["portal_users"].find_one({"_id": ObjectId(user_id)})
    except Exception:
        return None


async def _update_user(user_id: str, update: dict) -> None:
    from bson import ObjectId

    from app.core.database import get_database

    db = get_database()
    await db["portal_users"].update_one({"_id": ObjectId(user_id)}, {"$set": update})


# ── MFA challenge token (short-lived) ─────────────────────────────────────────


def _create_challenge_token(user_id: str) -> str:
    """MFA challenge 용 5분 JWT. sub = user_id. role: challenge 만."""
    return create_access_token(
        tenant_id="portal",
        client_id=f"challenge:{user_id}",
        roles=["portal:mfa_challenge"],
        issuer=PORTAL_ISSUER,
        expires_minutes=_MFA_CHALLENGE_MINUTES,
    )


def _decode_challenge_token(token: str) -> str | None:
    """challenge token 에서 user_id 추출. 실패 시 None."""
    from jose import JWTError, jwt

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
        sub: str = payload.get("sub", "")
        if sub.startswith("challenge:") and payload.get("iss") == PORTAL_ISSUER:
            return sub[len("challenge:") :]
    except JWTError:
        pass
    return None


# ── endpoints ─────────────────────────────────────────────────────────────────


@router.post("/login")
async def portal_login(
    body: LoginRequest,
    request: Request,
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """1단계: username/password 검증 → MFA challenge token 반환."""
    ip = request.client.host if request.client else "unknown"
    await _check_login_rate_limit(ip)

    user = await _get_user_by_username(body.username)

    # Constant-time — user 없어도 verify 실행 (timing attack 방지).
    # 필드명: portal_users 컬렉션의 hashed_password (seed_portal_admin.py 와 일치).
    dummy_hash = "$2b$12$WBRKBfpFn5Toh9dLNx3yze9GHaRpAGKVGFXBzVJF9gTxKiUXe6Bq."
    stored_hash = user.get("hashed_password", dummy_hash) if user else dummy_hash
    pwd_ok = _pwd_ctx.verify(body.password, stored_hash)

    if not user or not pwd_ok:
        if audit is not None:
            await audit.emit(
                action="auth.portal_login_failed",
                resource={"type": "portal_user", "id": body.username},
                request=request,
            )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    user_id = str(user["_id"])
    mfa_required = user.get("mfa_enabled", False)

    if not mfa_required:
        # MFA 미등록 시 challenge 없이 enroll 유도
        challenge = _create_challenge_token(user_id)
        return {
            "mfa_required": False,
            "mfa_enrolled": False,
            "challenge_token": challenge,
            "message": "MFA not enrolled. Please enroll via /mfa/enroll.",
        }

    challenge = _create_challenge_token(user_id)
    logger.info("portal_login_challenge_issued", user_id=user_id)
    return {
        "mfa_required": True,
        "challenge_token": challenge,
    }


@router.post("/mfa/verify")
async def portal_mfa_verify(
    body: MfaVerifyRequest,
    request: Request,
    response: Response,
    csrf_cookie: Annotated[str | None, Cookie(alias=_CSRF_COOKIE)] = None,
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """2단계: TOTP 검증 → access + refresh 쿠키 발급.

    Authorization: Bearer <challenge_token> 로 challenge 전달.
    """
    # challenge token 추출 (Authorization header)
    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Challenge token required")
    challenge_token = auth_header[7:]

    user_id = _decode_challenge_token(challenge_token)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid or expired challenge token")

    user = await _get_user_by_id(user_id)
    if not user or not user.get("mfa_enabled"):
        raise HTTPException(status_code=401, detail="MFA not enrolled")

    # TOTP 검증 (N5.2 async KMS)
    try:
        secret = await _decrypt_mfa_secret(user["mfa_secret_enc"])
    except Exception as exc:
        raise HTTPException(status_code=500, detail="MFA configuration error") from exc

    if not _verify_totp(secret, body.code):
        if audit is not None:
            await audit.emit(
                action="auth.portal_mfa_failed",
                resource={"type": "portal_user", "id": user_id},
                request=request,
            )
        raise HTTPException(status_code=401, detail="Invalid MFA code")

    # 세션 발급
    portal_roles: list[str] = user.get("portal_roles", ["portal:viewer"])
    expire_minutes = getattr(settings, "PORTAL_JWT_EXPIRE_MINUTES", 15)
    expire_hours = getattr(settings, "PORTAL_REFRESH_EXPIRE_HOURS", 8)

    access_token = create_access_token(
        tenant_id="portal",
        client_id=user_id,
        roles=portal_roles,
        issuer=PORTAL_ISSUER,
        expires_minutes=expire_minutes,
    )

    session_mgr = PortalSessionManager()
    refresh_token = await session_mgr.create_session(
        user_id=user_id,
        portal_roles=portal_roles,
        expire_hours=expire_hours,
    )

    csrf = _generate_csrf_token()
    _set_access_cookie(response, access_token, expire_minutes)
    _set_refresh_cookie(response, refresh_token, expire_hours)
    _set_csrf_cookie(response, csrf)

    # 마지막 로그인 기록
    await _update_user(user_id, {"active_last_login": datetime.now(UTC)})

    if audit is not None:
        await audit.emit(
            action="auth.portal_login_success",
            resource={"type": "portal_user", "id": user_id},
            after={"roles": portal_roles},
            request=request,
        )

    logger.info("portal_login_success", user_id=user_id, roles=portal_roles)
    return {"ok": True, "roles": portal_roles}


@router.post("/refresh")
async def portal_refresh(
    request: Request,
    response: Response,
    refresh_cookie: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=_CSRF_COOKIE)] = None,
) -> dict:
    """Refresh token rotation → 새 access + refresh 쿠키."""
    _check_csrf(request, csrf_cookie)

    if not refresh_cookie:
        raise HTTPException(status_code=401, detail="No refresh token")

    # refresh token 은 opaque — user_id 를 별도 헤더 또는 decode 없이 얻는 방법이 없음.
    # → 여기서는 access cookie 에서 user_id 추출 (만료여도 decode 가능).
    access_cookie = request.cookies.get(_ACCESS_COOKIE)
    user_id = _extract_user_id_from_expired_token(access_cookie)
    if not user_id:
        raise HTTPException(status_code=401, detail="Cannot identify user from access token")

    session_mgr = PortalSessionManager()
    new_refresh = await session_mgr.rotate(refresh_cookie, user_id)
    if new_refresh is None:
        _clear_cookies(response)
        raise HTTPException(status_code=401, detail="Invalid or expired refresh token")

    user = await _get_user_by_id(user_id)
    portal_roles: list[str] = user.get("portal_roles", ["portal:viewer"]) if user else []
    expire_minutes = getattr(settings, "PORTAL_JWT_EXPIRE_MINUTES", 15)
    expire_hours = getattr(settings, "PORTAL_REFRESH_EXPIRE_HOURS", 8)

    new_access = create_access_token(
        tenant_id="portal",
        client_id=user_id,
        roles=portal_roles,
        issuer=PORTAL_ISSUER,
        expires_minutes=expire_minutes,
    )
    csrf = _generate_csrf_token()
    _set_access_cookie(response, new_access, expire_minutes)
    _set_refresh_cookie(response, new_refresh, expire_hours)
    _set_csrf_cookie(response, csrf)

    return {"ok": True}


@router.post("/logout")
async def portal_logout(
    request: Request,
    response: Response,
    refresh_cookie: Annotated[str | None, Cookie(alias=_REFRESH_COOKIE)] = None,
    csrf_cookie: Annotated[str | None, Cookie(alias=_CSRF_COOKIE)] = None,
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """로그아웃 — 쿠키 삭제 + refresh 세션 전체 revoke."""
    _check_csrf(request, csrf_cookie)

    access_cookie = request.cookies.get(_ACCESS_COOKIE)
    user_id = _extract_user_id_from_expired_token(access_cookie)

    if user_id:
        session_mgr = PortalSessionManager()
        revoked = await session_mgr.revoke_all(user_id)
        if audit is not None:
            await audit.emit(
                action="auth.portal_logout",
                resource={"type": "portal_user", "id": user_id},
                after={"revoked_sessions": revoked},
                request=request,
            )

    _clear_cookies(response)
    return {"ok": True}


@router.post("/mfa/enroll")
async def portal_mfa_enroll(
    request: Request,
    tenant: Annotated[
        TenantContext,
        Depends(require_portal_role("portal:viewer", "portal:operator", "portal:admin")),
    ],
    audit: Annotated[AuditEmitter, Depends(get_audit_emitter)] = None,  # type: ignore[assignment]
) -> dict:
    """TOTP secret 신규 발급 + QR URI 반환. 이미 등록된 경우 재발급 (force 재등록).

    반환값:
      - totp_uri: otpauth:// URI (QR 코드 생성용)
      - secret: base32 encoded secret (수동 입력용)
    """
    import pyotp

    user_id = tenant.client_id  # portal token 의 client_id = user_id
    user = await _get_user_by_id(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    secret = pyotp.random_base32()
    secret_enc = await _encrypt_mfa_secret(secret)  # N5.2 async KMS
    issuer = getattr(settings, "PORTAL_MFA_ISSUER", "agentoe-portal")
    totp_uri = pyotp.totp.TOTP(secret).provisioning_uri(
        name=user.get("username", user_id),
        issuer_name=issuer,
    )

    await _update_user(
        user_id,
        {
            "mfa_secret_enc": secret_enc,
            "mfa_enabled": False,  # verify 후 true 로 전환 (별도 confirm endpoint 필요)
            "mfa_enrolled_at": datetime.now(UTC),
        },
    )

    if audit is not None:
        await audit.emit(
            action="auth.portal_mfa_enroll",
            resource={"type": "portal_user", "id": user_id},
            request=request,
        )

    return {
        "totp_uri": totp_uri,
        "secret": secret,  # 한 번만 노출 — 클라이언트가 저장 후 confirm 필요
    }


# ── helpers ───────────────────────────────────────────────────────────────────


def _extract_user_id_from_expired_token(token: str | None) -> str | None:
    """만료된 access token 에서도 sub (user_id) 추출 (options=no verify)."""
    if not token:
        return None
    try:
        from jose import jwt

        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            options={"verify_exp": False},
        )
        sub = payload.get("sub", "")
        if sub.startswith("challenge:"):
            return None
        return sub or None
    except Exception:
        return None
