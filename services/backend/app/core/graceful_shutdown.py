"""Graceful Shutdown Manager — 배포/스케일인 시 통화 유지.

설계:
  - SIGTERM 수신 → readiness 실패(=K8s가 트래픽 차단) → drain
  - Active 세션에 "시스템 점검 안내" TTS 송출(선택) → 자연 종료 대기
  - SHUTDOWN_DRAIN_TIMEOUT_SECONDS 후 잔여 세션 강제 close + FSM ENDED 저장

사용법::

    # main.py
    from app.core.graceful_shutdown import shutdown_manager, register_signal_handlers
    register_signal_handlers()
    ...
    # lifespan shutdown
    await shutdown_manager.drain(orchestrator)

    # vbgw.py 세션 루프
    if shutdown_manager.is_draining:
        await send_shutdown_notice(session_id)
        break
"""

from __future__ import annotations

import asyncio
import contextlib
import signal
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

from app.core.config import settings

logger = structlog.get_logger(__name__)


class ShutdownManager:
    """전역 드레인 상태 플래그 + drain 유틸."""

    def __init__(self) -> None:
        self._draining: bool = False
        self._drain_started_at: float | None = None
        # orchestrator에서 active 세션 나열/종료 콜백을 등록
        self._session_enumerator: Callable[[], Coroutine[Any, Any, list[str]]] | None = None
        self._session_terminator: Callable[[str], Coroutine[Any, Any, None]] | None = None
        self._notice_sender: Callable[[str], Coroutine[Any, Any, None]] | None = None

    @property
    def is_draining(self) -> bool:
        return self._draining

    def register_session_callbacks(
        self,
        enumerator: Callable[[], Coroutine[Any, Any, list[str]]],
        terminator: Callable[[str], Coroutine[Any, Any, None]],
        notice_sender: Callable[[str], Coroutine[Any, Any, None]] | None = None,
    ) -> None:
        """orchestrator가 lifespan startup 시 호출."""
        self._session_enumerator = enumerator
        self._session_terminator = terminator
        self._notice_sender = notice_sender

    def mark_draining(self) -> None:
        """Readyz가 이걸 보고 503 반환 → K8s 서비스 엔드포인트에서 제외."""
        if self._draining:
            return
        loop = asyncio.get_event_loop()
        self._draining = True
        self._drain_started_at = loop.time()
        logger.warning("shutdown_drain_started", timeout=settings.SHUTDOWN_DRAIN_TIMEOUT_SECONDS)

    async def drain(self) -> None:
        """
        lifespan shutdown에서 호출. 타임아웃까지 active 세션 자연 종료 대기,
        잔여 세션은 강제 종료한다.
        """
        self.mark_draining()
        timeout = settings.SHUTDOWN_DRAIN_TIMEOUT_SECONDS

        if not self._session_enumerator:
            logger.info("shutdown_drain_skip_no_orchestrator")
            return

        # 1) 활성 세션 목록 확인
        try:
            active = await self._session_enumerator()
        except Exception as e:
            logger.error("shutdown_enumerate_failed", error=str(e))
            return

        if not active:
            logger.info("shutdown_no_active_sessions")
            return

        logger.info("shutdown_active_sessions", count=len(active), sessions=active[:10])

        # 2) 선택적 안내 TTS
        if settings.SHUTDOWN_ACTIVE_CALL_ANNOUNCE and self._notice_sender:
            await asyncio.gather(
                *(self._safe_notice(sid) for sid in active),
                return_exceptions=True,
            )

        # 3) 타임아웃까지 1초마다 drain 체크 — 자연 종료 기다림
        loop = asyncio.get_event_loop()
        start = loop.time()
        while loop.time() - start < timeout:
            try:
                remaining = await self._session_enumerator()
            except Exception:
                remaining = []
            if not remaining:
                logger.info(
                    "shutdown_drain_completed_naturally", elapsed_s=round(loop.time() - start, 2)
                )
                return
            await asyncio.sleep(1.0)

        # 4) 잔여 세션 강제 종료
        try:
            remaining = await self._session_enumerator()
        except Exception:
            remaining = []
        if remaining and self._session_terminator:
            logger.warning("shutdown_force_terminate", count=len(remaining))
            for sid in remaining:
                try:
                    await self._session_terminator(sid)
                except Exception as e:
                    logger.error("shutdown_terminate_failed", session_id=sid, error=str(e))

    async def _safe_notice(self, session_id: str) -> None:
        if not self._notice_sender:
            return
        try:
            await asyncio.wait_for(self._notice_sender(session_id), timeout=3.0)
        except Exception as e:
            logger.warning("shutdown_notice_failed", session_id=session_id, error=str(e))


shutdown_manager = ShutdownManager()


def register_signal_handlers() -> None:
    """uvicorn/K8s SIGTERM → drain flag on. 실제 drain은 lifespan shutdown에서."""
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        # Windows / 이벤트 루프 상태에 따라 실패 가능 — uvicorn 기본 핸들러에 의존
        with contextlib.suppress(NotImplementedError, RuntimeError):
            loop.add_signal_handler(sig, shutdown_manager.mark_draining)
