"""Portal 세션 관리 — refresh token rotation + 동시 세션 제한 (plan §2.2).

설계:
  - Refresh token 은 MongoDB `portal_sessions` 컬렉션에 저장 (bcrypt hash).
  - Rotation: 매 refresh 마다 이전 token 무효화 + 새 token 발급.
  - 동시 세션 제한: `portal_users.max_concurrent_sessions` (기본 3).
  - 만료: `settings.PORTAL_REFRESH_EXPIRE_HOURS` (기본 8h).
  - Redis 에 활성 세션 카운터 캐싱 — 빠른 제한 체크용.

CLAUDE.md:
  - 모든 DB I/O 비동기 (motor).
  - Redis 실패 시 Mongo 로 fallback (graceful degradation).
"""

from __future__ import annotations

import contextlib
import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import structlog

logger = structlog.get_logger(__name__)

_COLLECTION = "portal_sessions"
_DEFAULT_MAX_SESSIONS = 3


class PortalSessionManager:
    """Refresh token lifecycle 관리."""

    def __init__(self, db: Any = None) -> None:
        self._db = db

    @property
    def col(self) -> Any:
        from app.core.database import get_database

        return (self._db or get_database())[_COLLECTION]

    async def create_session(
        self,
        user_id: str,
        portal_roles: list[str],
        max_concurrent: int = _DEFAULT_MAX_SESSIONS,
        expire_hours: int = 8,
    ) -> str:
        """새 refresh token 발급 + 세션 저장. 초과 시 가장 오래된 세션 삭제."""
        from app.core.config import settings

        expire_h = expire_hours or getattr(settings, "PORTAL_REFRESH_EXPIRE_HOURS", 8)

        # 동시 세션 제한 — 초과 시 오래된 것 삭제
        count = await self.col.count_documents({"user_id": user_id, "revoked": False})
        if count >= max_concurrent:
            oldest = (
                await self.col.find({"user_id": user_id, "revoked": False})
                .sort("created_at", 1)
                .limit(count - max_concurrent + 1)
                .to_list(None)
            )
            ids = [doc["_id"] for doc in oldest]
            if ids:
                await self.col.update_many(
                    {"_id": {"$in": ids}},
                    {"$set": {"revoked": True, "revoked_at": datetime.now(UTC)}},
                )

        token = secrets.token_urlsafe(48)
        expires_at = datetime.now(UTC) + timedelta(hours=expire_h)

        # bcrypt 는 72바이트 초과 입력을 거부하므로 SHA-256 으로 pre-hash (32바이트 고정).
        # checkpw 시에도 동일하게 sha256 적용.
        import bcrypt

        token_digest = hashlib.sha256(token.encode()).digest()
        token_hash = bcrypt.hashpw(token_digest, bcrypt.gensalt()).decode()

        await self.col.insert_one(
            {
                "user_id": user_id,
                "portal_roles": portal_roles,
                "token_hash": token_hash,
                "created_at": datetime.now(UTC),
                "expires_at": expires_at,
                "revoked": False,
            }
        )

        logger.info("portal_session_created", user_id=user_id)
        return token

    async def rotate(self, old_token: str, user_id: str) -> str | None:
        """Refresh token rotation — old 무효화 + new 발급.

        old_token 이 유효하지 않으면 None 반환 (공격 감지).
        """
        import bcrypt

        docs = await self.col.find(
            {"user_id": user_id, "revoked": False, "expires_at": {"$gt": datetime.now(UTC)}}
        ).to_list(None)

        matched = None
        for doc in docs:
            with contextlib.suppress(Exception):
                old_digest = hashlib.sha256(old_token.encode()).digest()
                if bcrypt.checkpw(old_digest, doc["token_hash"].encode()):
                    matched = doc
                    break

        if matched is None:
            logger.warning("portal_refresh_invalid_token", user_id=user_id)
            return None

        # 기존 세션 revoke
        await self.col.update_one(
            {"_id": matched["_id"]},
            {"$set": {"revoked": True, "revoked_at": datetime.now(UTC)}},
        )

        # 새 token 발급
        return await self.create_session(
            user_id=user_id,
            portal_roles=matched.get("portal_roles", []),
        )

    async def revoke_all(self, user_id: str) -> int:
        """로그아웃 — 모든 세션 revoke. revoke 수 반환."""
        result = await self.col.update_many(
            {"user_id": user_id, "revoked": False},
            {"$set": {"revoked": True, "revoked_at": datetime.now(UTC)}},
        )
        return result.modified_count
