"""
Unit tests — OutboundEvent → AiResponse 매핑 + Servicer 엣지 케이스.

orchestrator/Mongo 의존 없이 매핑 함수와 metadata 추출만 검증.
"""

from __future__ import annotations

import base64
from unittest.mock import MagicMock

from app.grpc_server.voicebot_service import (
    _extract_metadata,
    _outbound_to_responses,
)
from app.grpc_stubs.voicebot import voicebot_pb2 as pb
from app.services.call_session_orchestrator import OutboundEvent

# ── _outbound_to_responses ────────────────────────────────────────────────


def test_mapping_normal_pipeline_emits_stt_tts_eot() -> None:
    audio = b"\x01\x02\x03"
    events = [
        OutboundEvent("stt_result", {"text": "안녕하세요", "is_final": True}),
        OutboundEvent("llm_chunk", {"text": "네 안녕하세요", "is_final": True}),
        OutboundEvent(
            "tts_ready",
            {
                "audio_b64": base64.b64encode(audio).decode(),
                "text": "네 안녕하세요",
            },
        ),
        OutboundEvent("pipeline_done", {"latency": {"total_ms": 1234}}),
    ]
    out = _outbound_to_responses(events)
    assert len(out) == 3, "expected exactly 3 (STT_RESULT, TTS_AUDIO, END_OF_TURN)"
    assert out[0].type == pb.AiResponse.STT_RESULT
    assert out[0].text_content == "안녕하세요"
    assert out[1].type == pb.AiResponse.TTS_AUDIO
    assert out[1].audio_data == audio
    assert out[1].text_content == "네 안녕하세요"
    assert out[2].type == pb.AiResponse.END_OF_TURN


def test_mapping_filters_internal_events() -> None:
    """state_change / connected / pong / llm_chunk 는 proto 무대응 — drop."""
    events = [
        OutboundEvent("state_change", {"state": "LISTENING"}),
        OutboundEvent("connected", {"session_id": "abc"}),
        OutboundEvent("pong", {}),
        OutboundEvent("llm_chunk", {"text": "..."}),
    ]
    out = _outbound_to_responses(events)
    assert out == [], "internal events should not surface as AiResponse"


def test_mapping_error_auto_appends_eot() -> None:
    """error 만 있고 pipeline_done 없으면 자동으로 END_OF_TURN 보내 클라가 stuck 안 되게."""
    events = [
        OutboundEvent("error", {"code": "PIPELINE_ERROR", "message": "STT 실패"}),
    ]
    out = _outbound_to_responses(events)
    assert len(out) == 2
    assert out[0].type == pb.AiResponse.STT_RESULT
    assert "[ERROR]" in out[0].text_content
    assert "STT 실패" in out[0].text_content
    assert out[1].type == pb.AiResponse.END_OF_TURN


def test_mapping_error_with_pipeline_done_no_dup_eot() -> None:
    events = [
        OutboundEvent("error", {"message": "X"}),
        OutboundEvent("pipeline_done", {}),
    ]
    out = _outbound_to_responses(events)
    eot_count = sum(1 for r in out if r.type == pb.AiResponse.END_OF_TURN)
    assert eot_count == 1


def test_mapping_transfer_update() -> None:
    events = [
        OutboundEvent("transfer_update", {"reason": "CB_OPEN", "message": "상담사로 연결합니다."}),
    ]
    out = _outbound_to_responses(events)
    assert len(out) == 1
    assert out[0].type == pb.AiResponse.STT_RESULT
    assert "[TRANSFER]" in out[0].text_content


def test_mapping_tts_ready_invalid_b64_does_not_crash() -> None:
    events = [
        OutboundEvent("tts_ready", {"audio_b64": "!!!not-base64!!!", "text": "x"}),
    ]
    out = _outbound_to_responses(events)
    # audio_b64 디코드 실패 시 audio_data 빈 bytes — 스트림 자체는 살림.
    assert len(out) == 1
    assert out[0].type == pb.AiResponse.TTS_AUDIO
    assert out[0].audio_data == b""


def test_mapping_tts_ready_empty_b64() -> None:
    events = [OutboundEvent("tts_ready", {"audio_b64": "", "text": "no audio"})]
    out = _outbound_to_responses(events)
    assert len(out) == 1
    assert out[0].audio_data == b""


# ── _extract_metadata ─────────────────────────────────────────────────────


def _ctx_with_metadata(items: list[tuple[str, str]]) -> MagicMock:
    ctx = MagicMock()
    ctx.invocation_metadata.return_value = items
    return ctx


def test_metadata_default_fallback() -> None:
    tenant, client, auth = _extract_metadata(_ctx_with_metadata([]))
    assert tenant == "default"
    assert client == "anonymous"
    assert auth == ""


def test_metadata_extracts_all() -> None:
    ctx = _ctx_with_metadata(
        [
            ("x-tenant-id", "acme-corp"),
            ("x-client-id", "client-001"),
            ("authorization", "Bearer token-abc-123"),
        ]
    )
    tenant, client, auth = _extract_metadata(ctx)
    assert tenant == "acme-corp"
    assert client == "client-001"
    assert auth == "token-abc-123"


def test_metadata_authorization_strips_bearer_case_insensitive() -> None:
    ctx = _ctx_with_metadata([("authorization", "bearer token-xyz")])
    _, _, auth = _extract_metadata(ctx)
    assert auth == "token-xyz"


def test_metadata_authorization_without_bearer_kept_as_is() -> None:
    ctx = _ctx_with_metadata([("authorization", "raw-token")])
    _, _, auth = _extract_metadata(ctx)
    assert auth == "raw-token"


# ── proto wire compatibility (벤더링된 stub 의 회귀 검증) ────────────────


def test_audio_chunk_roundtrip() -> None:
    chunk = pb.AudioChunk(
        session_id="call-123",
        audio_data=b"\x00\x01\x02",
        is_speaking=True,
        dtmf_digit="",
    )
    decoded = pb.AudioChunk()
    decoded.ParseFromString(chunk.SerializeToString())
    assert decoded.session_id == "call-123"
    assert decoded.audio_data == b"\x00\x01\x02"
    assert decoded.is_speaking is True


def test_ai_response_clear_buffer_flag_roundtrip() -> None:
    resp = pb.AiResponse(type=pb.AiResponse.TTS_AUDIO, clear_buffer=True)
    decoded = pb.AiResponse()
    decoded.ParseFromString(resp.SerializeToString())
    assert decoded.type == pb.AiResponse.TTS_AUDIO
    assert decoded.clear_buffer is True


def test_response_type_enum_values_match_proto() -> None:
    """proto enum 값 회귀 — vbgw 와 정렬되어야 함."""
    assert pb.AiResponse.STT_RESULT == 0
    assert pb.AiResponse.TTS_AUDIO == 1
    assert pb.AiResponse.END_OF_TURN == 2
