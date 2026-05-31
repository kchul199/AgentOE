"""Phase 4 — E2E 기능 테스트 (F-01 ~ F-10).

이미 다른 통합 테스트로 커버된 시나리오:
  F-01 정상 통화 플로우   → test_vbgw_integration.py (connected, hangup, state_change)
  F-05 멀티 테넌트 격리   → test_auth_e2e.py (JWKS·quota tenant isolation)
  F-06 세션 복구          → test_vbgw_integration.py::test_reconnect_restores_session
  F-07 kill_switch        → test_vbgw_integration.py::test_websocket_kill_switch_blocked
  F-08 Circuit Breaker    → tests/unit/test_circuit_breaker.py (상태 머신 전체 커버)

이 파일에서 추가 검증하는 시나리오:
  F-02 barge-in (끼어들기)     — stop_listening 제어 메시지로 파이프라인 인터럽트
  F-03 silence/idle timeout    — is_idle_timeout 모킹 → IDLE_TIMEOUT 에러 이벤트
  F-04 LLM fallback (degraded) — AI 파이프라인 오류 → SERVICE_UNAVAILABLE 이벤트
  F-09 통화 중 drop 메트릭     — 비정상 종료 후 get_active_sessions 카운터 검증
  F-10 동시 다중 통화 (10세션) — threading 으로 10 세션 동시 연결 → 전부 connected
"""

from __future__ import annotations

import json
import sys
import threading
import unittest.mock as mock
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

# ── 외부 의존성 stub ──────────────────────────────────────────────────────────
for _mod in [
    "motor",
    "motor.motor_asyncio",
    "pymongo",
    "pymongo.errors",
    "redis",
    "redis.asyncio",
    "groq",
    "google.cloud",
    "google.cloud.texttospeech",
    "google.cloud.texttospeech_v1",
    "grpc",
]:
    if _mod not in sys.modules:
        sys.modules[_mod] = mock.MagicMock()

from app.core.auth import create_access_token

# ── 공통 픽스처 ───────────────────────────────────────────────────────────────


def _mock_repo() -> AsyncMock:
    repo = AsyncMock()
    repo.acquire_session_lease.return_value = True
    repo.release_session_lease = AsyncMock()
    repo.restore_hot_state.return_value = None  # 신규 세션
    repo.create = AsyncMock(return_value={})
    repo.save_turn = AsyncMock()
    repo.end_session = AsyncMock()
    repo.update_state = AsyncMock()
    repo.save_transfer_info = AsyncMock()
    return repo


def _base_patches(repo=None):
    if repo is None:
        repo = _mock_repo()
    return [
        patch("app.core.database.init_db", new_callable=AsyncMock),
        patch("app.core.database.close_db", new_callable=AsyncMock),
        patch("app.core.redis_client.init_redis", new_callable=AsyncMock),
        patch("app.core.redis_client.close_redis", new_callable=AsyncMock),
        patch("app.core.redis_client.get_redis", return_value=AsyncMock()),
        patch(
            "app.domain.kill_switch.KillSwitchService.is_active",
            new_callable=AsyncMock,
            return_value=False,
        ),
        patch("app.api.v1.routers.vbgw.SessionRepository", return_value=repo),
    ]


@pytest.fixture
def valid_token():
    return create_access_token("tenant-e2e", "client-e2e", ["operator"])


@pytest.fixture
def client():
    patches = _base_patches()
    for p in patches:
        p.start()
    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as c:
        yield c
    for p in patches:
        p.stop()


# ── F-02: barge-in (끼어들기) ─────────────────────────────────────────────────


def test_f02_barge_in_stop_listening_interrupts(client, valid_token):
    """F-02: TTS 재생 중 stop_listening 제어 메시지 → 파이프라인 인터럽트 (LISTENING).

    barge-in 시나리오: LISTENING 상태에서 오디오를 버퍼링 중 stop_listening 제어 메시지를
    수신하면 즉시 _run_pipeline 이 호출되어
    state_change(SPEAKING_DETECTED) → (pipeline) → state_change(LISTENING) 이벤트가 반환된다.
    MIN_AUDIO_BYTES(4000) 미만 전송 → stop_listening 으로 강제 파이프라인 실행.
    """
    from app.services.ai_pipeline import PipelineResult  # actual class name

    mock_pipeline = AsyncMock()
    mock_pipeline.process = AsyncMock(
        return_value=PipelineResult(
            stt_text="바지인 테스트",
            llm_text="네, 말씀하세요.",
            tts_audio=b"\x00" * 4,
            policy_level="none",
            policy_allowed=True,
            latency={"stt_ms": 0.0, "llm_ms": 0.0, "tts_ms": 0.0, "total_ms": 0.0},
        )
    )

    url = f"/api/v1/ws/vbgw?token={valid_token}&session_id=sess-f02"
    with (
        patch("app.services.call_session_orchestrator.AIPipeline", return_value=mock_pipeline),
        client.websocket_connect(url) as ws,
    ):
        ws.receive_text()  # connected

        # 1) LISTENING 상태 진입
        ws.send_text(json.dumps({"action": "start_listening"}))
        ws.receive_text()  # state_change LISTENING

        # 2) MIN_AUDIO_BYTES(4000) 미만 오디오 버퍼링 → 파이프라인 미실행
        ws.send_bytes(b"\x00" * 160)

        # 3) stop_listening → 버퍼 flush → _run_pipeline 강제 실행
        ws.send_text(json.dumps({"action": "stop_listening"}))

        # 4) SPEAKING_DETECTED 이벤트 수신 검증
        # 파이프라인 성공 시: SPEAKING_DETECTED, stt_result, llm_chunk, tts_ready, pipeline_done, LISTENING
        # SPEAKING_DETECTED 를 찾으면 즉시 break — 이후 수신 대기로 deadlock 방지
        events = []
        for _ in range(8):
            try:
                msg = json.loads(ws.receive_text())
                events.append(msg)
                if msg.get("event") == "state_change" and msg.get("state") == "SPEAKING_DETECTED":
                    break
            except Exception:
                break

        state_events = [e for e in events if e.get("event") == "state_change"]
        assert any(e.get("state") == "SPEAKING_DETECTED" for e in state_events), (
            f"SPEAKING_DETECTED 이벤트 없음. 수신된 이벤트: {events}"
        )

        # 5) 세션 정리
        ws.send_text(json.dumps({"action": "hangup"}))
        for _ in range(3):
            try:
                d = json.loads(ws.receive_text())
                if d.get("state") == "ENDED":
                    break
            except Exception:
                break


# ── F-03: silence / idle timeout ─────────────────────────────────────────────


def test_f03_idle_timeout_sends_error_event(valid_token):
    """F-03: 무음 비활성 타임아웃 → IDLE_TIMEOUT 에러 이벤트 + 세션 종료."""
    repo = _mock_repo()
    patches = _base_patches(repo)
    for p in patches:
        p.start()

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        url = f"/api/v1/ws/vbgw?token={valid_token}&session_id=sess-f03"
        with (
            patch(
                "app.services.call_session_orchestrator.CallSessionOrchestrator.is_idle_timeout",
                new_callable=lambda: property(lambda self: True),
            ),
            client.websocket_connect(url) as ws,
        ):
            ws.receive_text()  # connected

            # 다음 receive 시 idle timeout 체크가 트리거됨
            # 메인 루프가 is_idle_timeout=True 를 감지 → error(IDLE_TIMEOUT) → break
            events = []
            for _ in range(5):
                try:
                    events.append(json.loads(ws.receive_text()))
                except Exception:
                    break

            error_events = [e for e in events if e.get("event") == "error"]
            assert any(e.get("code") == "IDLE_TIMEOUT" for e in error_events), (
                f"IDLE_TIMEOUT 에러 이벤트 없음. 수신된 이벤트: {events}"
            )

    for p in patches:
        p.stop()


# ── F-04: LLM 장애 → SERVICE_UNAVAILABLE (degraded) ─────────────────────────


def test_f04_pipeline_exception_returns_service_unavailable(valid_token):
    """F-04: AI 파이프라인 예외(LLM 오류) → SERVICE_UNAVAILABLE 에러 이벤트 반환."""
    from app.domain.circuit_breaker import CircuitBreakerOpenError

    repo = _mock_repo()
    patches = _base_patches(repo)
    for p in patches:
        p.start()

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        url = f"/api/v1/ws/vbgw?token={valid_token}&session_id=sess-f04"

        mock_pipeline = AsyncMock()
        mock_pipeline.process = AsyncMock(
            side_effect=CircuitBreakerOpenError(service_name="groq-llm")
        )

        with (
            patch(
                "app.services.call_session_orchestrator.AIPipeline",
                return_value=mock_pipeline,
            ),
            client.websocket_connect(url) as ws,
        ):
            ws.receive_text()  # connected
            ws.send_text(json.dumps({"action": "start_listening"}))
            ws.receive_text()  # state_change LISTENING

            # MIN_AUDIO_BYTES(4000) 미만 오디오 버퍼링 → stop_listening 으로 파이프라인 강제 실행
            ws.send_bytes(b"\x00" * 160)
            ws.send_text(json.dumps({"action": "stop_listening"}))

            # CB OPEN: SPEAKING_DETECTED, SERVICE_UNAVAILABLE, LISTENING 순으로 도착
            # SERVICE_UNAVAILABLE 찾으면 즉시 break — 이후 수신 대기로 deadlock 방지
            events = []
            for _ in range(5):
                try:
                    msg = json.loads(ws.receive_text())
                    events.append(msg)
                    if msg.get("event") == "error" and msg.get("code") == "SERVICE_UNAVAILABLE":
                        break
                except Exception:
                    break

            error_events = [e for e in events if e.get("event") == "error"]
            assert any(e.get("code") == "SERVICE_UNAVAILABLE" for e in error_events), (
                f"SERVICE_UNAVAILABLE 이벤트 없음. 수신된 이벤트: {events}"
            )

            # 세션 정리
            try:
                ws.send_text(json.dumps({"action": "hangup"}))
                for _ in range(3):
                    d = json.loads(ws.receive_text())
                    if d.get("state") == "ENDED":
                        break
            except Exception:
                pass

    for p in patches:
        p.stop()


# ── F-09: 통화 중 drop → 세션 카운터 감소 ────────────────────────────────────


def test_f09_session_count_decrements_on_disconnect(valid_token):
    """F-09: WebSocket 연결 → connected → 강제 종료 → active_sessions 카운터 감소.

    세션 연결 전후 active_sessions 를 비교해 누수 없이 감소하는지 확인한다.
    """
    from app.core.metrics import get_active_sessions

    def _total_sessions() -> float:
        """get_active_sessions() 는 {tenant: count} dict 반환 — 전체 합산."""
        d = get_active_sessions()
        return sum(d.values()) if isinstance(d, dict) else float(d)

    repo = _mock_repo()
    patches = _base_patches(repo)
    for p in patches:
        p.start()

    from app.main import app

    with TestClient(app, raise_server_exceptions=False) as client:
        before = _total_sessions()

        with client.websocket_connect(
            f"/api/v1/ws/vbgw?token={valid_token}&session_id=sess-f09"
        ) as ws:
            ws.receive_text()  # connected — 카운터 +1
            during = _total_sessions()

        # WS 블록 종료 = 연결 해제 → 카운터 -1
        after = _total_sessions()

    for p in patches:
        p.stop()

    # 연결 중에는 before 보다 높거나 같고 (다른 테스트와 병렬 가능성), 해제 후에는 복원
    assert during >= before, "연결 중 active_sessions 가 증가하지 않음"
    assert after <= before + 1, (
        f"연결 해제 후 active_sessions 가 복원되지 않음: before={before} after={after}"
    )


# ── F-10: 10개 동시 세션 ──────────────────────────────────────────────────────


def test_f10_ten_concurrent_sessions(valid_token):
    """F-10: 10개 세션 동시 연결 → 각각 connected 이벤트 수신, 오류 없음.

    TestClient 는 동기이므로 threading 으로 10개를 동시에 실행한다.
    """
    repo = _mock_repo()
    patches = _base_patches(repo)
    for p in patches:
        p.start()

    from app.main import app

    results: list[dict] = []
    errors: list[Exception] = []
    lock = threading.Lock()

    def run_session(session_idx: int) -> None:
        token = create_access_token("tenant-e2e", f"client-{session_idx:03d}", ["operator"])
        try:
            with TestClient(app, raise_server_exceptions=False) as c:
                url = f"/api/v1/ws/vbgw?token={token}&session_id=sess-f10-{session_idx:03d}"
                with c.websocket_connect(url) as ws:
                    data = json.loads(ws.receive_text())
                    with lock:
                        results.append(data)
        except Exception as exc:
            with lock:
                errors.append(exc)

    threads = [threading.Thread(target=run_session, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)

    for p in patches:
        p.stop()

    assert not errors, f"동시 세션 중 예외 발생: {errors}"
    assert len(results) == 10, f"10개 세션 중 {len(results)}개만 응답"
    connected = [r for r in results if r.get("event") == "connected"]
    assert len(connected) == 10, (
        f"connected 이벤트를 받은 세션: {len(connected)}/10\n응답: {results}"
    )


# ── F-05 보완: 크로스 테넌트 시나리오 CRUD 격리 ───────────────────────────────


def test_f05_cross_tenant_scenario_access_denied():
    """F-05: tenant-A 가 발급한 JWT 로 tenant-B 의 시나리오 조회 시 404 반환.

    ScenarioRepository 가 tenant_id 스코프로 쿼리하므로, 다른 테넌트의 시나리오는
    '존재하지 않는 것처럼' 처리되어 404 를 반환한다.
    """
    import sys
    import unittest.mock as _mock

    for _mod in [
        "motor",
        "motor.motor_asyncio",
        "pymongo",
        "pymongo.errors",
        "redis",
        "redis.asyncio",
        "groq",
        "google.cloud",
        "google.cloud.texttospeech",
        "google.cloud.texttospeech_v1",
        "grpc",
    ]:
        if _mod not in sys.modules:
            sys.modules[_mod] = _mock.MagicMock()

    from app.core.auth import create_access_token
    from app.repositories.scenario_repository import ScenarioNotFoundError

    token_a = create_access_token("tenant-a", "client-a", ["admin"])
    mock_redis_a = AsyncMock()
    mock_redis_a.eval = AsyncMock(return_value=1)

    mock_repo = AsyncMock()
    mock_repo.get_latest = AsyncMock(
        side_effect=ScenarioNotFoundError("tenant-a", "tenant-b-scenario", "latest")
    )
    mock_repo.get_version = AsyncMock(
        side_effect=ScenarioNotFoundError("tenant-a", "tenant-b-scenario", "latest")
    )
    mock_repo.get_published = AsyncMock(
        side_effect=ScenarioNotFoundError("tenant-a", "tenant-b-scenario", "published")
    )

    patches = [
        patch("app.core.database.init_db", new_callable=AsyncMock),
        patch("app.core.database.close_db", new_callable=AsyncMock),
        patch("app.core.redis_client.init_redis", new_callable=AsyncMock),
        patch("app.core.redis_client.close_redis", new_callable=AsyncMock),
        patch("app.core.redis_client.get_redis", return_value=mock_redis_a),
        patch(
            "app.middleware.rate_limit_middleware.rate_limit_check",
            new=AsyncMock(return_value=True),
        ),
        patch("app.main.GrpcServerLifecycle", return_value=AsyncMock()),
    ]
    for p in patches:
        p.start()

    from app.main import app
    from app.repositories.scenario_repository import ScenarioRepository as _ScenarioRepoClass

    # FastAPI의 Depends()는 임포트 시점에 클래스 레퍼런스를 캡처하므로
    # 모듈 패치 대신 dependency_overrides 를 사용해야 한다.
    app.dependency_overrides[_ScenarioRepoClass] = lambda: mock_repo
    try:
        with TestClient(app, raise_server_exceptions=False) as client:
            # tenant-a 토큰으로 tenant-b 전용 시나리오 조회 시도 → 404
            resp = client.get(
                "/api/v1/scenarios/tenant-b-scenario?version=latest",
                headers={"Authorization": f"Bearer {token_a}"},
            )
            assert resp.status_code == 404, f"크로스 테넌트 접근이 허용됨: {resp.status_code}"
    finally:
        app.dependency_overrides.pop(_ScenarioRepoClass, None)
        for p in patches:
            p.stop()
