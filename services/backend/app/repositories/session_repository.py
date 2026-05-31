"""
Session Repository — MongoDB + Redis 이중 레이어
- MongoDB: 영구 저장 (session 레코드, 대화 히스토리, FSM 스냅샷)
- Redis: hot-state 캐시 (재연결 복구 P0 경로) + lease lock

설계 원칙:
  읽기 경로: Redis 우선 → 캐시 미스 시 MongoDB fallback
  쓰기 경로: MongoDB 선기록 → Redis 동기화 (write-through)
  Lease Lock: 동일 session_id 중복 연결 방지 (30초 TTL)

DI 설계:
  __init__(db=None) 시 즉시 get_database()를 호출하여 컬렉션을 _col/_history_col에
  캐시합니다. 테스트에서는 db=mock_db를 주입하여 전역 의존성을 완전히 차단합니다.
  col/history_col 프로퍼티는 캐시된 값을 반환하므로 매 접근마다 get_database()를
  호출하지 않습니다.

Lease 해제 책임:
  Lease는 vbgw.py finally 블록에서 단일 경로로 해제합니다.
  end_session()은 세션 데이터(MongoDB + Redis hot-state) 정리만 담당합니다.

save_turn 최적화:
  find_one_and_update(return_document=BEFORE)로 세션 업데이트와 turn_index 획득을
  단일 MongoDB 왕복으로 처리합니다 (기존 별도 SELECT 제거).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from typing import Any

from motor.motor_asyncio import AsyncIOMotorCollection
from pymongo import DESCENDING, ReturnDocument

from app.core.database import get_database
from app.core.exceptions import SessionNotFoundError
from app.core.redis_client import (
    acquire_lease,
    delete_session_state,
    get_session_state,
    release_lease,
    set_session_state,
)
from app.domain.session_fsm import SessionFSM

logger = logging.getLogger(__name__)

# 히스토리 최대 보존 턴 수 (MongoDB)
MAX_HISTORY_TURNS = 100
# Redis에 캐시할 히스토리 최근 턴 수 (빠른 재연결용)
REDIS_HISTORY_TURNS = 20


class SessionRepository:
    """
    세션 영속성 레이어.

    사용 패턴:
        # 프로덕션 — DB는 lifespan에서 init_db()로 초기화된 전역 인스턴스
        repo = SessionRepository()

        # 테스트 — mock DB 직접 주입
        repo = SessionRepository(db=mock_db)

        # 세션 생성
        await repo.create(session_data)

        # 재연결 복구 (Redis → MongoDB fallback)
        state = await repo.restore_hot_state(session_id)

        # 파이프라인 완료 후 상태 저장
        await repo.save_turn(session_id, fsm, user_text, ai_text)

        # 세션 종료
        await repo.end_session(session_id)
    """

    def __init__(self, db: Any = None) -> None:
        # 생성자에서 즉시 resolve — 이후 col/history_col은 캐시된 인스턴스를 반환.
        # db=None 시 get_database()를 호출하여 lifespan에서 초기화된 전역 DB를 사용.
        # 테스트에서 db=mock_db를 주입하면 get_database() 호출이 완전히 차단됩니다.
        _db = db or get_database()
        self._col: AsyncIOMotorCollection = _db["sessions"]
        self._history_col: AsyncIOMotorCollection = _db["session_history"]

    @property
    def col(self) -> AsyncIOMotorCollection:
        """캐시된 sessions 컬렉션."""
        return self._col

    @property
    def history_col(self) -> AsyncIOMotorCollection:
        """캐시된 session_history 컬렉션."""
        return self._history_col

    # ── 생성 ───────────────────────────────────────────────────────────────────

    async def create(self, session_data: dict[str, Any]) -> dict[str, Any]:
        """새 세션 레코드 생성 (MongoDB + Redis)."""
        now = datetime.now(UTC)
        session_data.setdefault("created_at", now)
        session_data.setdefault("updated_at", now)
        session_data.setdefault("status", "IDLE")
        session_data.setdefault("fsm_snapshot", {"state": "IDLE", "events": []})
        session_data.setdefault("history_count", 0)
        session_data.setdefault("transfer_info", None)

        await self._col.insert_one(session_data)

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
        doc = await self._col.find_one({"session_id": session_id}, {"_id": 0})
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
        total = await self._col.count_documents(query)
        cursor = (
            self._col.find(query, {"_id": 0})
            .sort("created_at", DESCENDING)
            .skip(offset)
            .limit(limit)
        )
        items = await cursor.to_list(length=limit)
        return items, total

    # ── 재연결 복구 (핵심) ─────────────────────────────────────────────────────

    async def restore_hot_state(self, session_id: str) -> dict[str, Any] | None:
        """
        재연결 시 세션 상태 복원.
        경로 1: Redis hit → 즉시 반환 (P0 초저지연)
        경로 2: Redis miss → MongoDB fallback → Redis 재워밍
        경로 3: MongoDB miss 또는 ENDED 상태 → None 반환

        반환값이 None인 두 경우를 구분하려면 get_by_id()를 직접 호출하세요.
        """
        # 1. Redis 우선 조회
        hot = await get_session_state(session_id)
        if hot:
            # ENDED 세션은 재연결 거부 (Redis 캐시에 남아있어도 동일)
            if hot.get("status") == "ENDED":
                logger.info(
                    "Reconnect rejected: session already ENDED (Redis cache)",
                    extra={"session_id": session_id},
                )
                return None
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
        - MongoDB: 세션 업데이트 + 히스토리 문서 삽입
        - Redis: hot-state 갱신

        find_one_and_update(return_document=BEFORE)로 세션 업데이트와 turn_index 획득을
        단일 왕복으로 처리합니다. BEFORE 반환값의 history_count가 이 턴의 인덱스입니다.
        """
        now = datetime.now(UTC)
        fsm_snapshot = fsm.to_snapshot()

        # ── MongoDB: 세션 업데이트 + turn_index 원자적 획득 ──────────────
        before = await self._col.find_one_and_update(
            {"session_id": session_id},
            {
                "$set": {
                    "status": fsm.state.value,
                    "fsm_snapshot": fsm_snapshot,
                    "updated_at": now,
                },
                "$inc": {"history_count": 1},
            },
            projection={"history_count": 1, "_id": 0},
            return_document=ReturnDocument.BEFORE,  # 업데이트 전 history_count = turn_index
        )
        # $inc 이전 값이 이 턴의 0-based 인덱스 (0번 턴 → history_count=0 반환 → index=0)
        turn_index = (before or {}).get("history_count", 0)

        # ── MongoDB: history 컬렉션에 턴 삽입 ────────────────────────────
        await self._history_col.insert_one(
            {
                "session_id": session_id,
                "turn_index": turn_index,
                "user_text": user_text,
                "ai_text": ai_text,
                "policy_level": policy_level,
                "latency": latency or {},
                "created_at": now,
            }
        )

        # ── Redis: hot-state 갱신 (최근 REDIS_HISTORY_TURNS 턴 유지) ──────
        hot = await get_session_state(session_id) or {}
        history: list = hot.get("history", [])
        history.append({"role": "user", "content": user_text})
        history.append({"role": "assistant", "content": ai_text})
        if len(history) > REDIS_HISTORY_TURNS * 2:
            history = history[-(REDIS_HISTORY_TURNS * 2) :]
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
        now = datetime.now(UTC)
        update: dict[str, Any] = {
            "status": state,
            "updated_at": now,
        }
        if extra:
            update.update(extra)
        if fsm:
            update["fsm_snapshot"] = fsm.to_snapshot()

        result = await self._col.update_one(
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
        """
        세션 종료: MongoDB 상태 업데이트 + Redis hot-state 삭제.

        Lease 해제는 이 메서드의 책임이 아닙니다.
        Lease는 항상 vbgw.py finally 블록에서 repo.release_session_lease()로 해제됩니다.
        (Lease 획득도 vbgw.py에서 했으므로 해제도 같은 계층이 담당합니다.)
        """
        now = datetime.now(UTC)
        update: dict[str, Any] = {
            "status": "ENDED",
            "ended_at": now,
            "updated_at": now,
            "end_reason": reason,
        }
        if fsm:
            update["fsm_snapshot"] = fsm.to_snapshot()

        await self._col.update_one(
            {"session_id": session_id},
            {"$set": update},
        )
        # Redis hot-state 삭제 (ENDED 세션은 캐시 불필요)
        await delete_session_state(session_id)
        logger.info(
            "Session ended",
            extra={"session_id": session_id, "reason": reason},
        )

    # ── Lease Lock ─────────────────────────────────────────────────────────────

    async def acquire_session_lease(self, session_id: str, tenant_id: str | None = None) -> bool:
        """
        세션 lease 획득 시도.
        동일 session_id로 중복 WebSocket 연결 방지.
        Returns True if acquired, False if already locked.
        """
        return await acquire_lease(session_id, tenant_id=tenant_id)

    async def release_session_lease(self, session_id: str, tenant_id: str | None = None) -> None:
        """
        세션 lease 해제.
        vbgw.py finally 블록에서 단일 경로로 호출됩니다.
        """
        await release_lease(session_id, tenant_id=tenant_id)

    # ── 이관 정보 저장 ──────────────────────────────────────────────────────────

    async def save_transfer_info(
        self,
        session_id: str,
        transfer_info: dict[str, Any],
    ) -> None:
        """상담사 이관 정보 MongoDB에 저장."""
        now = datetime.now(UTC)
        await self._col.update_one(
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
            self._history_col.find(
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
