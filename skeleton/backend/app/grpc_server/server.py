"""
gRPC server lifecycle — FastAPI lifespan 에 wire.

설계:
  - grpc.aio.server 1개. FastAPI uvicorn loop 와 동일 asyncio loop 공유.
    → orchestrator, Mongo motor, Redis async client 모두 같은 loop 에서 동작.
  - Health probe: grpc_health_v1 표준. K8s gRPC liveness/readiness 가 사용 가능.
  - Reflection: 개발용 (`grpcurl describe`). prod 에선 settings.GRPC_REFLECTION_ENABLED
    로 끔.
  - Graceful shutdown: SIGTERM → server.stop(grace) → 진행 중 stream 자연 종료
    대기 (Helm preStop 의 30초 grace 와 정합).
"""
from __future__ import annotations

import asyncio
from typing import Final

import grpc
import structlog
from grpc_health.v1 import health, health_pb2, health_pb2_grpc

from app.core.config import settings
from app.grpc_server.voicebot_service import VoicebotAiServicer
from app.grpc_stubs.voicebot import voicebot_pb2_grpc as pb_grpc
from app.repositories.session_repository import SessionRepository

logger = structlog.get_logger(__name__)

GRPC_PORT: Final[int] = int(getattr(settings, "GRPC_PORT", 50051))
GRPC_MAX_CONCURRENT_STREAMS: Final[int] = int(
    getattr(settings, "GRPC_MAX_CONCURRENT_STREAMS", 200)
)
GRPC_GRACEFUL_SHUTDOWN_SEC: Final[float] = float(
    getattr(settings, "GRPC_GRACEFUL_SHUTDOWN_SEC", 25.0)
)


def build_server(repo: SessionRepository) -> grpc.aio.Server:
    """
    grpc.aio.Server 인스턴스 구성. start() / stop() 은 호출자 책임.
    """
    server = grpc.aio.server(
        options=[
            ("grpc.max_concurrent_streams", GRPC_MAX_CONCURRENT_STREAMS),
            # keepalive — bridge 가 idle 상태에서도 끊기지 않게.
            ("grpc.keepalive_time_ms", 30_000),
            ("grpc.keepalive_timeout_ms", 10_000),
            ("grpc.keepalive_permit_without_calls", 1),
            ("grpc.http2.min_time_between_pings_ms", 10_000),
            ("grpc.http2.min_ping_interval_without_data_ms", 5_000),
            # 프레임 크기 — 20ms PCM(16kHz) = 640 byte. 기본 limit 충분하지만 명시.
            ("grpc.max_send_message_length", 16 * 1024 * 1024),
            ("grpc.max_receive_message_length", 16 * 1024 * 1024),
        ]
    )

    # 1) VoicebotAiService
    pb_grpc.add_VoicebotAiServiceServicer_to_server(
        VoicebotAiServicer(repo=repo), server,
    )

    # 2) Health check (grpc_health_v1)
    health_servicer = health.HealthServicer()
    health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)
    health_servicer.set(
        "voicebot.ai.VoicebotAiService",
        health_pb2.HealthCheckResponse.SERVING,
    )
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)

    # 3) Reflection (선택)
    if getattr(settings, "GRPC_REFLECTION_ENABLED", False):
        try:
            from grpc_reflection.v1alpha import reflection
            from app.grpc_stubs.voicebot import voicebot_pb2 as pb
            SERVICE_NAMES = (
                pb.DESCRIPTOR.services_by_name["VoicebotAiService"].full_name,
                health.SERVICE_NAME,
                reflection.SERVICE_NAME,
            )
            reflection.enable_server_reflection(SERVICE_NAMES, server)
        except ImportError:
            logger.warning("grpc-reflection 미설치 — reflection 비활성")

    server.add_insecure_port(f"[::]:{GRPC_PORT}")
    return server


class GrpcServerLifecycle:
    """
    FastAPI lifespan 에서 사용할 wrapper.

    예:
      grpc_lifecycle = GrpcServerLifecycle(repo)
      @app.on_event("startup")  # 또는 lifespan
      async def _start(): await grpc_lifecycle.start()
      @app.on_event("shutdown")
      async def _stop(): await grpc_lifecycle.stop()
    """

    def __init__(self, repo: SessionRepository) -> None:
        self._repo = repo
        self._server: grpc.aio.Server | None = None
        self._serve_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._server is not None:
            logger.warning("gRPC server already started — skip")
            return
        self._server = build_server(self._repo)
        await self._server.start()
        logger.info("gRPC server listening", port=GRPC_PORT,
                    service="voicebot.ai.VoicebotAiService")

    async def stop(self) -> None:
        if self._server is None:
            return
        logger.info("gRPC server stopping",
                    grace_sec=GRPC_GRACEFUL_SHUTDOWN_SEC)
        await self._server.stop(grace=GRPC_GRACEFUL_SHUTDOWN_SEC)
        self._server = None
        logger.info("gRPC server stopped")

    @property
    def is_running(self) -> bool:
        return self._server is not None
