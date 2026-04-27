"""
agentoe-vbgw — minimal scaffold.

이 파일은 CI 가 빌드할 수 있는 최소 진입점이고, 실제 SIP/RTP / gRPC 음성 처리 로직은
별도 PR 에서 구현 예정. 다음 책임 분리만 잡혀 있다:

  - aiohttp HTTP 서버 (8080) — health/drain
  - Prometheus exporter (9100) — /metrics
  - gRPC server (50051) — backend ↔ vbgw RPC (placeholder reflection only)
  - WebSocket server (50052) — 음성 frame 양방향
  - graceful shutdown — SIGTERM → drain → 진행 중 통화 자연 종료 대기
"""
from __future__ import annotations

import asyncio
import os
import signal
from typing import Final

import grpc
from aiohttp import web
from grpc_health.v1 import health, health_pb2, health_pb2_grpc
from prometheus_client import (
    CONTENT_TYPE_LATEST,
    Counter,
    Gauge,
    Histogram,
    generate_latest,
    start_http_server,
)

GRPC_PORT: Final[int] = int(os.getenv("GRPC_PORT", "50051"))
WS_PORT: Final[int] = int(os.getenv("WS_PORT", "50052"))
HEALTH_PORT: Final[int] = int(os.getenv("HEALTH_PORT", "8080"))
METRICS_PORT: Final[int] = int(os.getenv("METRICS_PORT", "9100"))
MAX_CONCURRENT_CALLS: Final[int] = int(os.getenv("MAX_CONCURRENT_CALLS", "50"))

# ── 메트릭 — 고정 라벨 없음 (라벨 폭발 방지) ──────────────────────────
ACTIVE_CALLS = Gauge("agentoe_active_calls", "현재 처리 중인 통화 수")
TOTAL_CALLS = Counter("agentoe_total_calls", "누적 통화 수")

# ── SLO 측정용 시리즈 (docs/reference/slo.md 와 일치) ───────────────
# call setup: ok 면 통화 셋업 성공. fail 은 코덱 협상 실패 / 인증 실패 / backend 거부 등.
CALL_SETUP = Counter(
    "agentoe_call_setup_total",
    "Call setup attempts by result",
    ["result"],   # ok | fail
)
# 통화 종료 사유 — SLO mid-call drop = (network|server_error|crash) / setup_ok
CALL_TERMINATIONS = Counter(
    "agentoe_call_terminations_total",
    "Call terminations by reason",
    ["reason"],   # normal | client_hangup | network | server_error | crash | timeout
)
# 단일 통화 길이 (SLO 분포 분석용)
CALL_DURATION = Histogram(
    "agentoe_call_duration_seconds",
    "Call duration in seconds",
    buckets=(5, 15, 30, 60, 120, 300, 600, 1200, 1800, 3600),
)

# ── shared mutable state ─────────────────────────────────────────────────
DRAINING = False
ACTIVE: set[asyncio.Task[None]] = set()


# ── /healthz/* HTTP ────────────────────────────────────────────────────
async def healthz_live(_: web.Request) -> web.Response:
    return web.json_response({"status": "ok"})


async def healthz_ready(_: web.Request) -> web.Response:
    if DRAINING:
        return web.json_response({"status": "draining"}, status=503)
    return web.json_response({"status": "ready", "active_calls": len(ACTIVE)})


async def healthz_drain(_: web.Request) -> web.Response:
    """preStop 훅이 호출 → readiness 503 으로 새 통화 차단."""
    global DRAINING
    DRAINING = True
    return web.json_response({"draining": True})


async def healthz_active_calls(_: web.Request) -> web.Response:
    return web.Response(text=str(len(ACTIVE)))


async def metrics_handler(_: web.Request) -> web.Response:
    return web.Response(body=generate_latest(), content_type=CONTENT_TYPE_LATEST)


def build_http() -> web.Application:
    app = web.Application()
    app.router.add_get("/healthz/live", healthz_live)
    app.router.add_get("/healthz/ready", healthz_ready)
    app.router.add_post("/healthz/drain", healthz_drain)
    app.router.add_get("/healthz/active_calls", healthz_active_calls)
    app.router.add_get("/metrics", metrics_handler)
    return app


# ── gRPC server (placeholder + grpc-health) ─────────────────────────────
async def serve_grpc() -> grpc.aio.Server:
    server = grpc.aio.server(options=[("grpc.so_reuseport", 0)])
    health_servicer = health.HealthServicer()
    await health_servicer.set("", health_pb2.HealthCheckResponse.SERVING)  # type: ignore[func-returns-value]
    health_pb2_grpc.add_HealthServicer_to_server(health_servicer, server)
    server.add_insecure_port(f"0.0.0.0:{GRPC_PORT}")
    await server.start()
    return server


# ── WebSocket placeholder — 실제 음성 처리는 후속 PR ─────────────────
async def ws_handler(_request: web.Request) -> web.WebSocketResponse:  # pragma: no cover
    """
    WS 한 번 = 통화 한 번. SLO 시리즈 발화 책임:
      - setup ok/fail
      - active 게이지 inc/dec
      - termination reason (정상/네트워크/서버 오류)
      - call duration histogram
    """
    ws = web.WebSocketResponse()
    setup_ok = False
    start_t = asyncio.get_event_loop().time()
    termination_reason = "normal"
    try:
        await ws.prepare(_request)
        setup_ok = True
        CALL_SETUP.labels(result="ok").inc()
        TOTAL_CALLS.inc()
        ACTIVE_CALLS.inc()
        async for msg in ws:
            # 실제 음성 frame 처리는 후속 PR — 여기선 단순 echo 카운트
            if msg.type == web.WSMsgType.ERROR:
                termination_reason = "network"
                break
    except Exception:
        # prepare 단계 실패 또는 핸들러 내 예외 → 통화 끊김
        if not setup_ok:
            CALL_SETUP.labels(result="fail").inc()
            return ws
        termination_reason = "server_error"
        raise
    finally:
        if setup_ok:
            ACTIVE_CALLS.dec()
            CALL_TERMINATIONS.labels(reason=termination_reason).inc()
            CALL_DURATION.observe(asyncio.get_event_loop().time() - start_t)
    return ws


def build_ws() -> web.Application:
    app = web.Application()
    app.router.add_get("/ws", ws_handler)
    return app


async def main() -> None:
    # health/metrics HTTP
    health_runner = web.AppRunner(build_http())
    await health_runner.setup()
    await web.TCPSite(health_runner, "0.0.0.0", HEALTH_PORT).start()

    # WebSocket
    ws_runner = web.AppRunner(build_ws())
    await ws_runner.setup()
    await web.TCPSite(ws_runner, "0.0.0.0", WS_PORT).start()

    # Prometheus 별도 포트도 가능 (현재는 HTTP /metrics 와 통일)
    # start_http_server(METRICS_PORT)   # 필요 시 활성화

    grpc_server = await serve_grpc()

    # ── signal handling ────────────────────────────────────────────────
    stop_evt = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop_evt.set)

    print(f"[vbgw] up — grpc={GRPC_PORT} ws={WS_PORT} health={HEALTH_PORT}", flush=True)
    await stop_evt.wait()

    print("[vbgw] SIGTERM → draining", flush=True)
    global DRAINING
    DRAINING = True
    # 진행 중 통화 자연 종료 대기 — Helm preStop 이 active_calls 폴링하므로 짧게.
    await asyncio.sleep(2)
    await grpc_server.stop(grace=5)
    await ws_runner.cleanup()
    await health_runner.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
