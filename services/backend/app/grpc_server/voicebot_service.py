"""
VoicebotAiService — vbgw bridge ↔ AgentOE backend gRPC bidi 스트리밍.

Contract: skeleton/contracts/proto/voicebot.proto

흐름:
  bridge ──AudioChunk(20ms)──▶ Servicer.StreamSession
                                      │
                                      │ audio_data 누적 (handle_audio)
                                      │ is_speaking false 전이 시 → 파이프라인 실행
                                      │
                                      ▼
                              CallSessionOrchestrator
                                      │
                                      │ STT → PolicyGate → LLM → TTS
                                      │ → OutboundEvent 시퀀스
                                      │
                              AiResponse 매핑 (stream)
                                      │
  bridge ◀──AiResponse(STT_RESULT)──┤
  bridge ◀──AiResponse(TTS_AUDIO)───┤
  bridge ◀──AiResponse(END_OF_TURN)─┘

설계 포인트:
  1. **세션 1개 = 스트림 1개.** session_id 는 SIP Call-ID. 동일 ID 재연결은
     orchestrator hot-state 복구로 처리 (restore_or_create_orchestrator).
  2. **Barge-in:** 클라이언트가 새 발화 (is_speaking=true) 보내면 즉시
     AiResponse(clear_buffer=true) 응답해 bridge 의 출력 버퍼 플러시 지시.
  3. **DTMF:** dtmf_digit 가 있으면 음성 buffering 우회, 별도 control 처리.
     (현재 v0: control event 기록만, 후속 버전이 IVR 라우팅)
  4. **인증/테넌트:** gRPC metadata 의 `x-tenant-id`, `authorization` 헤더에서
     추출. 비어 있으면 `default` 테넌트로 fallback (개발 모드).
  5. **에러:** 파이프라인 실패는 OutboundEvent("error") → AiResponse 의
     text_content 에 에러 메시지 + END_OF_TURN. gRPC status 는 healthy
     유지 (스트림 자체 종료 X — 다음 발화 시도 가능).
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import time
from collections.abc import AsyncIterator
from typing import Final

import grpc
import structlog

from app.core.metrics import (
    dec_active_sessions,
    inc_active_sessions,
)
from app.domain.policy_gate import PolicyLevel
from app.grpc_server.metrics import (
    grpc_chunk_received,
    grpc_session_end,
    grpc_session_start,
    record_call_duration,
    record_call_setup,
    record_call_termination,
)
from app.grpc_stubs.voicebot import voicebot_pb2 as pb
from app.grpc_stubs.voicebot import voicebot_pb2_grpc as pb_grpc
from app.repositories.session_repository import SessionRepository
from app.services.call_session_orchestrator import (
    OutboundEvent,
    restore_or_create_orchestrator,
)

logger = structlog.get_logger(__name__)

# 메타데이터 키
META_TENANT: Final[str] = "x-tenant-id"
META_AUTH: Final[str] = "authorization"
META_CLIENT_ID: Final[str] = "x-client-id"

# 발화 종료 판단 — bridge 의 VAD 가 false 보내면 즉시 flush.
# (이중 안전망: 무발화 8초 지속 시 강제 flush — bridge VAD 오작동 대비)
SILENCE_FORCE_FLUSH_SEC: Final[float] = 8.0


def _extract_metadata(context: grpc.aio.ServicerContext) -> tuple[str, str, str]:
    """
    gRPC metadata 에서 tenant_id / client_id / auth_token 추출.
    누락 시 dev fallback ("default", "anonymous", "").
    """
    md = dict(context.invocation_metadata())
    tenant = md.get(META_TENANT, "default") or "default"
    client = md.get(META_CLIENT_ID, "anonymous") or "anonymous"
    auth = md.get(META_AUTH, "") or ""
    if auth.lower().startswith("bearer "):
        auth = auth[7:]
    return tenant, client, auth


def _outbound_to_responses(events: list[OutboundEvent]) -> list[pb.AiResponse]:
    """
    Orchestrator OutboundEvent 시퀀스 → AiResponse 시퀀스 매핑.

    매핑 표:
      stt_result      → AiResponse(STT_RESULT, text_content=text)
      tts_ready       → AiResponse(TTS_AUDIO, audio_data=decoded, text_content=text)
      pipeline_done   → AiResponse(END_OF_TURN)
      error           → AiResponse(STT_RESULT, text_content=err) + END_OF_TURN
      transfer_update → AiResponse(STT_RESULT, text_content=msg) + END_OF_TURN
      state_change/connected/reconnected/pong/llm_chunk → 무시 (proto 와 무관)
    """
    out: list[pb.AiResponse] = []
    saw_pipeline_done = False
    saw_terminal_error = False

    for ev in events:
        name = ev.name
        p = ev.payload

        if name == "stt_result":
            out.append(
                pb.AiResponse(
                    type=pb.AiResponse.STT_RESULT,
                    text_content=p.get("text", ""),
                )
            )

        elif name == "tts_ready":
            audio_b64 = p.get("audio_b64", "")
            try:
                audio_bytes = base64.b64decode(audio_b64) if audio_b64 else b""
            except Exception:
                audio_bytes = b""
                logger.warning("tts_ready audio_b64 decode failed")
            out.append(
                pb.AiResponse(
                    type=pb.AiResponse.TTS_AUDIO,
                    text_content=p.get("text", ""),
                    audio_data=audio_bytes,
                )
            )

        elif name == "pipeline_done":
            saw_pipeline_done = True
            out.append(pb.AiResponse(type=pb.AiResponse.END_OF_TURN))

        elif name == "error":
            saw_terminal_error = True
            msg = p.get("message", "오류가 발생했습니다.")
            out.append(
                pb.AiResponse(
                    type=pb.AiResponse.STT_RESULT,
                    text_content=f"[ERROR] {msg}",
                )
            )

        elif name == "transfer_update":
            msg = p.get("message", "상담사로 연결합니다.")
            out.append(
                pb.AiResponse(
                    type=pb.AiResponse.STT_RESULT,
                    text_content=f"[TRANSFER] {msg}",
                )
            )

        # state_change / connected / reconnected / pong / llm_chunk — skip

    # 에러 발생했지만 pipeline_done 누락 시 강제 END_OF_TURN — bridge 가
    # 무한 대기 안 하도록.
    if saw_terminal_error and not saw_pipeline_done:
        out.append(pb.AiResponse(type=pb.AiResponse.END_OF_TURN))

    return out


class VoicebotAiServicer(pb_grpc.VoicebotAiServiceServicer):
    """
    StreamSession 핸들러.

    의존성:
      - SessionRepository — Mongo + Redis 상태 저장 (constructor 주입)

    상태:
      각 스트림이 자체 orchestrator 를 가짐 (서비서는 stateless).
    """

    def __init__(self, repo: SessionRepository) -> None:
        self._repo = repo

    async def StreamSession(
        self,
        request_iterator: AsyncIterator[pb.AudioChunk],
        context: grpc.aio.ServicerContext,
    ) -> AsyncIterator[pb.AiResponse]:
        tenant_id, client_id, _auth = _extract_metadata(context)
        session_id: str | None = None
        orchestrator = None
        last_speaking = False
        stream_start = time.monotonic()
        terminated_reason = "normal"

        # 응답 큐 — 서비서가 yield 하기 전에 응답 모음 (orchestrator 호출 결과)
        response_queue: asyncio.Queue[pb.AiResponse | None] = asyncio.Queue()

        async def emit(events: list[OutboundEvent]) -> None:
            for resp in _outbound_to_responses(events):
                await response_queue.put(resp)

        async def reader() -> None:
            """request_iterator 를 소비하며 orchestrator 에 위임."""
            nonlocal session_id, orchestrator, last_speaking, terminated_reason
            try:
                async for chunk in request_iterator:
                    grpc_chunk_received(tenant=tenant_id)

                    # 첫 청크 — session_id 확인 + lease 획득 + orchestrator init
                    if session_id is None:
                        session_id = chunk.session_id or "anonymous"
                        if not session_id:
                            terminated_reason = "server_error"
                            await response_queue.put(
                                pb.AiResponse(
                                    type=pb.AiResponse.STT_RESULT,
                                    text_content="[ERROR] missing session_id",
                                )
                            )
                            await response_queue.put(pb.AiResponse(type=pb.AiResponse.END_OF_TURN))
                            return

                        # ── Lease Lock 획득 ──────────────────────────────
                        # 동일 session_id 중복 연결 방지.
                        print(
                            f"DEBUG: Attempting to acquire lease for {session_id} (tenant={tenant_id})"
                        )
                        if not await self._repo.acquire_session_lease(
                            session_id, tenant_id=tenant_id
                        ):
                            print(f"DEBUG: Lease acquisition FAILED for {session_id}")
                            terminated_reason = "lease_conflict"
                            logger.warning(
                                "Duplicate session rejected (lease lock)", session_id=session_id
                            )
                            await response_queue.put(
                                pb.AiResponse(
                                    type=pb.AiResponse.STT_RESULT,
                                    text_content="[ERROR] session already in progress (lease lock)",
                                )
                            )
                            await response_queue.put(pb.AiResponse(type=pb.AiResponse.END_OF_TURN))
                            # session_id를 None으로 되돌려 finally에서 해제되지 않게 함
                            # (원래 잡고 있는 쪽이 해제해야 하므로)
                            session_id = None
                            return

                        orchestrator, _initial = await restore_or_create_orchestrator(
                            session_id=session_id,
                            tenant_id=tenant_id,
                            client_id=client_id,
                            repo=self._repo,
                            policy_level=PolicyLevel.G1,
                        )
                        if orchestrator is None:
                            terminated_reason = "server_error"
                            await response_queue.put(
                                pb.AiResponse(
                                    type=pb.AiResponse.STT_RESULT,
                                    text_content="[ERROR] session ended",
                                )
                            )
                            await response_queue.put(pb.AiResponse(type=pb.AiResponse.END_OF_TURN))
                            return

                        record_call_setup(result="ok")
                        grpc_session_start(tenant=tenant_id)
                        inc_active_sessions(tenant_id)
                        # initial event (connected/reconnected) 는 proto 무대응 — drop.
                        # listening 시작 control 자동 송신
                        await emit(await orchestrator.handle_control({"action": "start_listening"}))

                    assert orchestrator is not None
                    assert session_id is not None

                    # ── DTMF ───────────────────────────────────────────────
                    if chunk.dtmf_digit:
                        # v0: control message 만 기록. IVR 라우팅은 후속 phase.
                        await emit(
                            await orchestrator.handle_control(
                                {
                                    "action": "dtmf",
                                    "digit": chunk.dtmf_digit,
                                }
                            )
                        )
                        continue

                    # ── 음성 frame ─────────────────────────────────────────
                    is_speaking = bool(chunk.is_speaking)

                    # Barge-in 감지: 이전엔 silence, 지금 발화 시작.
                    # bridge 출력 버퍼 즉시 비우라고 알림.
                    if is_speaking and not last_speaking:
                        await response_queue.put(
                            pb.AiResponse(
                                type=pb.AiResponse.TTS_AUDIO,
                                clear_buffer=True,
                            )
                        )

                    # 발화 끝 감지: 이전엔 발화, 지금 silence — flush 트리거.
                    if (not is_speaking) and last_speaking:
                        await emit(await orchestrator.handle_control({"action": "stop_listening"}))
                        # 다음 발화 위해 다시 listening 켬
                        await emit(await orchestrator.handle_control({"action": "start_listening"}))
                    elif chunk.audio_data:
                        # buffer 누적 — 임계 도달 시 자동 pipeline 호출됨
                        await emit(await orchestrator.handle_audio(chunk.audio_data))

                    last_speaking = is_speaking

                # 클라가 닫음 → 정상 종료
                terminated_reason = "client_hangup"
            except asyncio.CancelledError:
                terminated_reason = "client_hangup"
                raise
            except Exception as exc:
                terminated_reason = "server_error"
                logger.exception(
                    "StreamSession reader error", session_id=session_id, error=str(exc)
                )
                await response_queue.put(
                    pb.AiResponse(
                        type=pb.AiResponse.STT_RESULT,
                        text_content=f"[ERROR] internal: {exc}",
                    )
                )
                await response_queue.put(pb.AiResponse(type=pb.AiResponse.END_OF_TURN))
            finally:
                # sentinel — writer 가 종료 알 수 있게
                await response_queue.put(None)

        reader_task = asyncio.create_task(reader())

        try:
            while True:
                resp = await response_queue.get()
                if resp is None:
                    break
                yield resp
        finally:
            # cleanup
            reader_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await reader_task

            # ── Lease Lock 해제 + FSM end + 메트릭 ──────────────────────
            if session_id:
                try:
                    await self._repo.release_session_lease(session_id, tenant_id=tenant_id)
                except Exception as exc:
                    logger.warning(
                        "release_session_lease failed", session_id=session_id, error=str(exc)
                    )

                if orchestrator is not None:
                    try:
                        end_events = await orchestrator.end_session(reason=terminated_reason)
                        # end_session 의 이벤트는 굳이 클라에 보낼 필요 없음 (이미 stream 닫힘)
                        _ = end_events
                    except Exception as exc:
                        logger.warning("end_session failed", session_id=session_id, error=str(exc))

                dec_active_sessions(tenant_id)
                grpc_session_end(tenant=tenant_id, reason=terminated_reason)
                record_call_termination(reason=terminated_reason)
                record_call_duration(time.monotonic() - stream_start)

            if session_id is None:
                # 시작 전 실패 (lease_conflict 등)
                record_call_setup(result="fail")
