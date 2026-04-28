#!/usr/bin/env python3
"""
smoke_grpc_client.py — bridge 없이 backend gRPC 만 빠르게 검증.

목적:
  운영 cutover 전, AgentOE backend 의 VoicebotAiService 가 wire-level 로
  정상 동작하는지 확인. bridge 의 동작을 mimic 한다:
    - 첫 청크에 session_id 포함
    - 발화 chunk 들 (is_speaking=true) 송신
    - 마지막에 silence 청크 (is_speaking=false) → 파이프라인 트리거
    - 응답 stream 에서 STT_RESULT, TTS_AUDIO, END_OF_TURN 검증

사용:
  python3 smoke_grpc_client.py                          # localhost:50051
  python3 smoke_grpc_client.py --addr backend.local:50051
  python3 smoke_grpc_client.py --tenant acme --calls 3

종료 코드:
  0 — wire OK (END_OF_TURN 받음)
  1 — connection 실패
  2 — 응답 contract 불일치
  3 — timeout
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
import uuid
from pathlib import Path

# backend 저장소 안에서 실행 — app/ import 가능하게
ROOT = Path(__file__).resolve().parents[2]   # 
sys.path.insert(0, str(ROOT / "backend"))

import grpc                                                          # noqa: E402
from app.grpc_stubs.voicebot import voicebot_pb2 as pb               # noqa: E402
from app.grpc_stubs.voicebot import voicebot_pb2_grpc as pb_grpc     # noqa: E402
from grpc_health.v1 import health_pb2, health_pb2_grpc               # noqa: E402

# ── 합성 음성 — 실 STT 호출은 외부 의존이므로 dev 모드에서는 dummy bytes
#    (파이프라인 안에서 Groq STT 가 dummy 받으면 degraded path 로 빠지지만,
#    그 자체도 contract 검증으로 의미 있음 — STT_RESULT 또는 [ERROR] 로 응답)
DUMMY_AUDIO_FRAME = b"\x00" * 640   # 16kHz mono PCM 20ms = 640 byte


async def _check_health(addr: str, timeout: float = 3.0) -> str:
    """grpc_health_v1 으로 SERVING 상태 확인. 실패 시 'NOT_SERVING' 또는 예외 메시지."""
    async with grpc.aio.insecure_channel(addr) as ch:
        stub = health_pb2_grpc.HealthStub(ch)
        try:
            resp = await asyncio.wait_for(
                stub.Check(health_pb2.HealthCheckRequest(
                    service="voicebot.ai.VoicebotAiService"
                )),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            return "TIMEOUT"
        except grpc.aio.AioRpcError as e:
            return f"ERROR: {e.code().name} {e.details()}"
    name = health_pb2.HealthCheckResponse.ServingStatus.Name(resp.status)
    return name


async def _one_call(
    addr: str,
    tenant: str,
    timeout_sec: float,
    speaking_chunks: int,
) -> tuple[bool, dict]:
    """
    한 통화 stream 을 mimic. 반환 (ok, summary).

    summary keys: session_id, n_stt, n_tts, n_eot, n_clear, n_other,
                  errors (list[str]), elapsed_sec
    """
    session_id = f"smoke-{uuid.uuid4().hex[:12]}"
    summary = {
        "session_id": session_id,
        "n_stt": 0, "n_tts": 0, "n_eot": 0, "n_clear": 0, "n_other": 0,
        "errors": [],
    }
    metadata = (
        ("x-tenant-id", tenant),
        ("x-client-id", "smoke-client"),
        ("authorization", "Bearer smoke-token"),
    )
    start = time.monotonic()

    async def request_iter():
        # 1) 발화 시작 청크들 — bridge 가 보내는 패턴 모방
        for i in range(speaking_chunks):
            yield pb.AudioChunk(
                session_id=session_id,
                audio_data=DUMMY_AUDIO_FRAME,
                is_speaking=True,
            )
            await asyncio.sleep(0.02)   # 20ms 청크 간격
        # 2) silence 청크 — 발화 종료 신호 (파이프라인 트리거)
        yield pb.AudioChunk(
            session_id=session_id,
            audio_data=b"",
            is_speaking=False,
        )
        # 3) bridge 가 통화 끝낸 시뮬레이션 — 짧게 대기 후 stream half-close
        await asyncio.sleep(0.5)

    async with grpc.aio.insecure_channel(addr) as ch:
        stub = pb_grpc.VoicebotAiServiceStub(ch)
        call = stub.StreamSession(request_iter(), metadata=metadata)

        try:
            async def reader():
                async for resp in call:
                    if resp.clear_buffer:
                        summary["n_clear"] += 1
                    if resp.type == pb.AiResponse.STT_RESULT:
                        summary["n_stt"] += 1
                    elif resp.type == pb.AiResponse.TTS_AUDIO:
                        summary["n_tts"] += 1
                    elif resp.type == pb.AiResponse.END_OF_TURN:
                        summary["n_eot"] += 1
                        return  # END_OF_TURN 받으면 종료
                    else:
                        summary["n_other"] += 1

            await asyncio.wait_for(reader(), timeout=timeout_sec)
        except asyncio.TimeoutError:
            summary["errors"].append(f"timeout after {timeout_sec}s")
            return False, summary
        except grpc.aio.AioRpcError as e:
            summary["errors"].append(f"grpc {e.code().name}: {e.details()}")
            return False, summary

    summary["elapsed_sec"] = round(time.monotonic() - start, 3)

    # contract 검증: 최소 END_OF_TURN 1개 + (STT_RESULT 또는 TTS_AUDIO 중 하나)
    if summary["n_eot"] == 0:
        summary["errors"].append("no END_OF_TURN received")
        return False, summary
    if summary["n_stt"] == 0 and summary["n_tts"] == 0:
        summary["errors"].append("no STT_RESULT and no TTS_AUDIO — pipeline silent?")
        return False, summary
    return True, summary


def _print_summary(idx: int, ok: bool, s: dict) -> None:
    flag = "[OK]" if ok else "[FAIL]"
    print(f"{flag} call#{idx} session={s['session_id']} "
          f"STT={s['n_stt']} TTS={s['n_tts']} EOT={s['n_eot']} "
          f"clear={s['n_clear']} other={s['n_other']} "
          f"elapsed={s.get('elapsed_sec', '?')}s")
    for err in s["errors"]:
        print(f"      ↳ {err}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--addr", default="localhost:50051",
                    help="backend gRPC 주소 (기본 localhost:50051)")
    ap.add_argument("--tenant", default="default")
    ap.add_argument("--calls", type=int, default=1, help="합성 통화 수")
    ap.add_argument("--speaking-chunks", type=int, default=10,
                    help="발화 chunk 수 (1 chunk=20ms)")
    ap.add_argument("--timeout", type=float, default=20.0,
                    help="한 통화의 응답 대기 timeout (초)")
    ap.add_argument("--skip-health", action="store_true")
    args = ap.parse_args()

    print(f"=== AgentOE backend gRPC smoke ===")
    print(f"target = {args.addr}   tenant = {args.tenant}   calls = {args.calls}")

    # ── 1) health check ────────────────────────────────────────────────
    if not args.skip_health:
        status = await _check_health(args.addr)
        print(f"[health] voicebot.ai.VoicebotAiService → {status}")
        if status != "SERVING":
            print("→ 서비스가 SERVING 아님. backend 시작 + grpc port 도달 확인 필요.")
            return 1

    # ── 2) reflection sanity (선택) ────────────────────────────────────
    try:
        from grpc_reflection.v1alpha.reflection_pb2 import ServerReflectionRequest
        from grpc_reflection.v1alpha.reflection_pb2_grpc import ServerReflectionStub
        async with grpc.aio.insecure_channel(args.addr) as ch:
            stub = ServerReflectionStub(ch)
            async def req():
                yield ServerReflectionRequest(list_services="")
            seen = []
            async for r in stub.ServerReflectionInfo(req()):
                if r.HasField("list_services_response"):
                    seen = [s.name for s in r.list_services_response.service]
                    break
            if seen:
                print(f"[reflection] services: {', '.join(sorted(seen))}")
            else:
                print("[reflection] 비활성 (GRPC_REFLECTION_ENABLED=false 일 가능성)")
    except Exception as e:  # noqa: BLE001
        print(f"[reflection] skipped: {e!s}")

    # ── 3) 합성 통화 ──────────────────────────────────────────────────
    fails = 0
    for i in range(1, args.calls + 1):
        ok, summary = await _one_call(
            args.addr, args.tenant,
            timeout_sec=args.timeout,
            speaking_chunks=args.speaking_chunks,
        )
        _print_summary(i, ok, summary)
        if not ok:
            fails += 1

    print(f"\n=== result: {args.calls - fails}/{args.calls} OK ===")
    if fails:
        print("→ 실패가 있다. backend 로그 확인:")
        print(f"   docker logs --since 2m agentoe-api | jq")
        return 2 if any("contract" in s for s in []) else 1
    print("→ wire OK. cutover 절차 진행 가능.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)
