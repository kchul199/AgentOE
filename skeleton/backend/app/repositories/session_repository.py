"""
Session Repository — MongoDB + Redis 이중 레이어
- MongoDB: 영구 저장 (session 레코드, 대화 히스토리, FSM 스냅샷)
- Redis: hot-state 캐시 (재연결 복구 P0 경로) + lease lock

설계 원칙:
  읽기 경로: Redis 우선 → 캐시 미스 시 MongoDB fallback
  쓰기 경로: MongoDB 선기록 → Redis 동기화 (write-through)
  Lease Lock: 동일 session_id 중복 연결 방지 (30초 TTL)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any

from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import DESCENDING

from app.core.database import get_database
from app.core.exceptions import SessionNotFoundError
from app.core.redis_client import (
    acquire_lease,
    delete_session_state,
    get_session_state,
    release_lease,
    set_session_state,
)
from app.domain.session_fsm import SessionFSM, SessionEventType

logger = logging.getLogger(__name__)

# 히스토리 최대 보존 턴 수 (MongoDB)
MAX_HISTORY_TURNS = 100
# Redis에 캐시할 히스토리 최근 턴 수 (빠른 재연결용)
REDIS_HISTORY_TURNS = 20


class SessionRepository:
    """
    세션 영속성 레이어.

    사용 패턴:
        repo = SessionRepository()

        # 세션 생성
        await repo.create(session_data)

        # 재연결 복구 (Redis → MongoDB fallback)
        state = await repo.restore_hot_state(session_id)

        # 파이프라인 완료 후 상태 저장
        await repo.save_turn(session_id, fsm_snapshot, user_text, ai_text)

        # 세션 종료
        await repo.end_session(session_id)
    """

    def __init__(self, db: AsyncIOMotorDatabase | None = None) -> None:
        self._db = db

    @property
    def col(self):
        db = self._db or get_database()
        return db["sessions"]

    @property
    def history_col(self):
        db = self._db or get_database()
        return db["session_history"]

    # ── 생성 ───────────────────────────────────────────────────────────────────

    async def create(self, session_data: dict[str, Any]) -> dict[str, Any]:
        """새 세션 레코드 생성 (MongoDB + Redis)."""
        now = datetime.now(timezone.utc)
        session_data.setdefault("created_at", now)
        session_data.setdefault("updated_at", now)
        session_data.setdefault("status", "IDLE")
        session_data.setdefault("fsm_snapshot", {"state": "IDLE", "events": []})
        session_data.setdefault("history_count", 0)
        session_data.setdefault("transfer_info", None)

        await self.col.insert_one(session_data)

        # Redis hot-state 초기화
        await set_session_state(
            session_data["session_id"],
            {
                "status": session_data["status"],
                "fsm_snapshot": session_data["fsm_snapshot"],
                "history": [],
                "tenant_id": session_data.get("tenant_id", ""),
                "client_id": session_data.get("client_id", ""),
            },
        )

        logger.info(
            "Session created",
            extra={
                "session_id": session_data.get("session_id"),
                "tenant_id": session_data.get("tenant_id"),
            },
        )
        return session_data

    # ── 조회 ───────────────────────────────────────────────────────────────────

    async def get_by_id(self, session_id: str) -> dict[str, Any]:
        """MongoDB에서 세션 레코드 조회."""
        doc = await self.col.find_one({"session_id": session_id}, {"_id": 0})
        if not doc:
            raise SessionNotFoundError(session_id)
        return doc

    async def list_by_tenant(
        self,
        tenant_id: str,
        status: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> tuple[list[dict], int]:
        """테넌트별 세션 목록 조회."""
        query: dict[str, Any] = {"tenant_id": tenant_id}
        if status:
            query["status"] = status
        total = await self.col.count_documents(query)
        cursor = (
            self.col.find(query, {"_id": 0})
            .sort("created_at", DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        items = await cursor.to_list(length=limit)
        return items, total

    # ── 재연결 복구 (핵심) ─────────────────────────────────────────────────────

    async def restore_hot_state(
        self, session_id: str
    ) -> dict[str, Any] | None:
        """
        재연결 시 세션 상태 복원.
        경로 1: Redis hit → 즉시 반환 (P0 초저지연)
        경로 2: Redis miss → MongoDB fallback → Redis 재워밍
        경로 3: MongoDB miss → None 반환 (신규 세션 처리)
        """
        # 1. Redis 우선 조회
        hot = await get_session_state(session_id)
        if hot:
            logger.debug("Session restored from Redis", extra={"session_id": session_id})
            return hot

        # 2. MongoDB fallback
        try:
            doc = await self.get_by_id(session_id)
        except SessionNotFoundError:
            return None

        if doc.get("status") == "ENDED":
            logger.info(
                "Reconnect rejected: session already ENDED",
                extra={"session_id": session_id},
            )
            return None

        # 3. 최근 히스토리 불러오기
        history = await self.get_recent_history(session_id, limit=REDIS_HISTORY_TURNS)

        # 4. Redis 재워밍
        hot_state = {
            "status": doc.get("status", "IDLE"),
            "fsm_snapshot": doc.get("fsm_snapshot", {"state": "IDLE", "events": []}),
            "history": history,
            "tenant_id": doc.get("tenant_id", ""),
            "client_id": doc.get("client_id", ""),
        }
        await set_session_state(session_id, hot_state)
        logger.info(
            "Session restored from MongoDB (cache warmed)",
            extra={"session_id": session_id, "turns": len(history)},
        )
        return hot_state

    # ── 턴 저장 (파이프라인 완료 후 호출) ──────────────────────────────────────

    async def save_turn(
        self,
        session_id: str,
        fsm: SessionFSM,
        user_text: str,
        ai_text: str,
        latency: dict | None = None,
        policy_level: str = "G1",
    ) -> None:
        """
        AI 파이프라인 1턴 완료 후 상태 저장.
        - MongoDB: 히스토리 문서 삽입 + 세션 업데이트
        - Redis: hot-state 갱신
        """
        now = datetime.now(timezone.utc)
        fsm_snapshot = fsm.to_snapshot()

        # MongoDB history 컬렉션에 턴 삽입
        turn = {
            "session_id": session_id,
            "turn_index": await self._next_turn_index(session_id),
            "user_text": user_text,
            "ai_text": ai_text,
            "policy_level": policy_level,
            "latency": latency or {},
            "created_at": now,
        }
        await self.history_col.insert_one(turn)

        # MongoDB 세션 업데이트
        await self.col.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "status": fsm.state.value,
                    "fsm_snapshot": fsm_snapshot,
                    "updated_at": now,
                },
                "$inc": {"history_count": 1},
            },
        )

        # Redis hot-state 갱신 (최근 REDIS_HISTORY_TURNS 턴)
        hot = await get_session_state(session_id) or {}
        history: list = hot.get("history", [])
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": ai_text})
        if len(history) > REDIS_HISTORY_TURNS * 2:
            history = history[-(REDIS_HISTORY_TURNS * 2):]
        hot["history"] = history
        hot["status"] = fsm.state.value
        hot["fsm_snapshot"] = fsm_snapshot
        await set_session_state(session_id, hot)

    # ── 상태 업데이트 (이관 등 단순 상태 변경) ─────────────────────────────────

    async def update_state(
        self,
        session_id: str,
        state: str,
        extra: dict | None = None,
        fsm: SessionFSM | None = None,
    ) -> None:
        """상태만 업데이트 (대화 히스토리 변경 없음)."""
        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {
            "status": state,
            "updated_at": now,
        }
        if extra:
            update.update(extra)
        if fsm:
            update["fsm_snapshot"] = fsm.to_snapshot()

        result = await self.col.update_one(
            {"session_id": session_id},
            {"$set": update},
        )
        if result.matched_count == 0:
            raise SessionNotFoundError(session_id)

        # Redis 동기화
        hot = await get_session_state(session_id) or {}
        hot["status"] = state
        if fsm:
            hot["fsm_snapshot"] = fsm.to_snapshot()
        if extra:
            hot.update(extra)
        await set_session_state(session_id, hot)

    # ── 종료 ───────────────────────────────────────────────────────────────────

    async def end_session(
        self,
        session_id: str,
        reason: str = "normal",
        fsm: SessionFSM | None = None,
    ) -> None:
        """세션 종료: MongoDB 업데이트 + Redis 캐시 삭제 + lease 해제."""
        now = datetime.now(timezone.utc)
        update: dict[str, Any] = {
            "status": "ENDED",
            "ended_at": now,
            "updated_at": now,
            "end_reason": reason,
        }
        if fsm:
            update["fsm_snapshot"] = fsm.to_snapshot()

        await self.col.update_one(
            {"session_id": session_id},
            {"$set": update},
        )
        # Redis hot-state 삭제 (ENDED 세션은 캐시 불필요)
        await delete_session_state(session_id)
        await release_lease(session_id)
        logger.info(
            "Session ended",
            extra={"session_id": session_id, "reason": reason},
        )

    # ── Lease Lock ─────────────────────────────────────────────────────────────

    async def acquire_session_lease(self, session_id: str) -> bool:
        """
        세션 lease 획득 시도.
        동일 session_id로 중복 WebSocket 연결 방지.
        Returns True if acquired, False if already locked.
        """
        return await acquire_lease(session_id)

    async def release_session_lease(self, session_id: str) -> None:
        """세션 lease 해제."""
        await release_lease(session_id)

    # ── 이관 정보 저장 ──────────────────────────────────────────────────────────

    async def save_transfer_info(
        self,
        session_id: str,
        transfer_info: dict[str, Any],
    ) -> None:
        """상담사 이관 정보 MongoDB에 저장."""
        now = datetime.now(timezone.utc)
        await self.col.update_one(
            {"session_id": session_id},
            {
                "$set": {
                    "transfer_info": {**transfer_info, "recorded_at": now},
                    "updated_at": now,
                }
            },
        )

    # ── 히스토리 조회 ──────────────────────────────────────────────────────────

    async def get_recent_history(
        self, session_id: str, limit: int = REDIS_HISTORY_TURNS
    ) -> list[dict]:
        """MongoDB에서 최근 N턴 히스토리를 LLM 형식으로 조회."""
        cursor = (
            self.history_col.find(
                {"session_id": session_id},
                {"_id": 0, "user_text": 1, "ai_text": 1, "turn_index": 1},
            )
            .sort("turn_index", DESCENDING)
            .limit(limit)
        )
        turns = await cursor.to_list(length=limit)
        # 시간순 정렬 후 LLM 형식으로 변환
        turns.reverse()
        history: list[dict] = []
        for t in turns:
            history.append({"role": "user", "content": t["user_text"]})
            history.append({"role": "assistant", "content": t["ai_text"]})
        return history

    # ── 내부 헬퍼 ──────────────────────────────────────────────────────────────

    async def _next_turn_index(self, session_id: str) -> int:
        """현재 세션의 다음 턴 인덱스 계산."""
        doc = await self.col.find_one(
            {"session_id": session_id},
            {"history_count": 1, "_id": 0},
        )
        return (doc or {}).get("history_count", 0)
