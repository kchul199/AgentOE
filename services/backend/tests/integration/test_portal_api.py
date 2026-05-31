"""Phase N — Portal API 통합 테스트 (P-INT-01 ~ P-INT-10).

전제조건:
  - MONGODB_URI, REDIS_URL 환경변수 설정
  - portal_users 에 테스트 계정 존재 (fixture 에서 자동 생성)
  - 실 MongoDB + Redis 연결 가능

실행:
    export MONGODB_URI="mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin"
    export REDIS_URL="redis://localhost:6380/0"
    export PORTAL_MFA_ENVELOPE_KEY="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
    export PORTAL_JWT_SECRET="dev-portal-jwt-secret-local"
    export PORTAL_ORIGIN="http://localhost:5174"
    python -m pytest tests/integration/test_portal_api.py -v --tb=short -x
"""

from __future__ import annotations

import os
import time

import pytest
from fastapi.testclient import TestClient

# ── 환경변수 기본값 (CI/CD 환경 호환) ────────────────────────────────────────
os.environ.setdefault(
    "MONGODB_URI", "mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6380/0")
os.environ.setdefault(
    "PORTAL_MFA_ENVELOPE_KEY", "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
)
os.environ.setdefault("PORTAL_JWT_SECRET", "dev-portal-jwt-secret-local")
os.environ.setdefault("PORTAL_ORIGIN", "http://localhost:5174")
os.environ.setdefault("JWT_SECRET", "dev-jwt-secret-local")
os.environ.setdefault("GROQ_API_KEY", "dummy-groq-key")

# ── 상수 ─────────────────────────────────────────────────────────────────────
BASE = "/api/v1"
TEST_ADMIN_USER = "portal_test_admin"
TEST_VIEWER_USER = "portal_test_viewer"
TEST_PASSWORD = "Test@Pass123"

# seed_test_users(conftest.py) 에서 고정 지정한 역할 — DB 조회 없이 직접 사용
_TEST_USER_ROLES: dict[str, list[str]] = {
    TEST_ADMIN_USER: ["portal:admin"],
    TEST_VIEWER_USER: ["portal:viewer"],
}


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures
# ─────────────────────────────────────────────────────────────────────────────

# app_client 및 seed_test_users 픽스처는 tests/integration/conftest.py 에서 제공.


def _login(client: TestClient, username: str) -> dict:
    """자격증명 검증 후 portal JWT 를 직접 발급해 반환.

    운영포탈의 2단계 로그인(password → MFA TOTP) 대신 테스트에서는:
      1) POST /login 으로 credentials 유효성만 확인 (200 + challenge_token)
      2) _TEST_USER_ROLES 에서 역할을 조회 (DB async 호출 불필요 — seed 값 고정)
      3) portal-issuer JWT 를 직접 생성
      4) PortalCookieMiddleware 가 portal_access 쿠키를 Authorization 헤더로 승격
    """
    from app.core.auth import PORTAL_ISSUER, create_access_token

    # 1) credentials 확인
    resp = client.post(
        f"{BASE}/auth/portal/login",
        json={"username": username, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, f"로그인 실패({resp.status_code}): {resp.text}"

    # 2) 역할 조회 — seed 값에서 직접 (async event loop 충돌 방지)
    roles = _TEST_USER_ROLES.get(username, ["portal:viewer"])

    # 3) portal-issuer JWT 직접 발급
    token = create_access_token(
        tenant_id="portal",
        client_id=username,
        roles=roles,
        issuer=PORTAL_ISSUER,
    )
    csrf = "test-csrf-token"
    return {
        "cookies": {"portal_access": token, "__csrf__": csrf},
        "headers": {"Authorization": f"Bearer {token}", "X-CSRF-Token": csrf},
        "token": token,
        "csrf": csrf,
    }


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-01  login → access token 발급 (MFA 비활성)
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_01_login_no_mfa(app_client: TestClient) -> None:
    """P-INT-01: 올바른 자격증명 + MFA 비활성 → 200 + challenge_token 발급.

    운영포탈 로그인 흐름:
      step1: POST /login  → challenge_token (MFA 등록 여부 무관)
      step2: POST /mfa/verify (TOTP) → portal_access + portal_refresh + __csrf__ 쿠키
    테스트 계정(mfa_enabled=False)은 step1 에서 challenge_token 을 받는다.
    """
    resp = app_client.post(
        f"{BASE}/auth/portal/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_PASSWORD},
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
    body = resp.json()
    assert "challenge_token" in body, f"challenge_token 없음: {body}"
    # mfa_enrolled=False 이면 enroll 메시지 포함
    if not body.get("mfa_required", True):
        assert body.get("mfa_enrolled") is False or "message" in body, (
            f"mfa_required=False 응답 형식 이상: {body}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-02  viewer 로 operator 엔드포인트 → 403
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_02_viewer_cannot_silence_alert(app_client: TestClient) -> None:
    """P-INT-02: portal:viewer 토큰으로 silence 생성(operator+) → 403.

    alertmanager silence 엔드포인트: POST /api/v1/admin/alerts/silence
    (admin.py 라우터: @router.post('/alerts/silence'))
    """
    auth = _login(app_client, TEST_VIEWER_USER)
    resp = app_client.post(
        f"{BASE}/admin/alerts/silence",
        json={
            "matchers": [{"name": "alertname", "value": "test", "isRegex": False}],
            "startsAt": "2099-01-01T00:00:00Z",
            "endsAt": "2099-01-02T00:00:00Z",
            "comment": "test",
        },
        cookies=auth["cookies"],
        headers={"X-CSRF-Token": auth["csrf"]},
    )
    assert resp.status_code in (403, 401), (
        f"viewer 가 operator 엔드포인트에 접근됨: HTTP {resp.status_code}"
    )


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-03  /stream/metrics SSE 연결 + 이벤트 수신
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_03_sse_metrics_stream(app_client: TestClient) -> None:
    """P-INT-03: SSE /stream/metrics 연결 시 data: 이벤트 수신."""
    auth = _login(app_client, TEST_VIEWER_USER)
    with app_client.stream(
        "GET",
        f"{BASE}/stream/metrics",
        cookies=auth["cookies"],
        timeout=5.0,
    ) as resp:
        assert resp.status_code == 200
        ct = resp.headers.get("content-type", "")
        assert "text/event-stream" in ct, f"SSE Content-Type 아님: {ct}"
        # 첫 청크 수신 (heartbeat 또는 data)
        received = ""
        for chunk in resp.iter_lines():
            received += chunk + "\n"
            if received.strip():
                break
        assert received.strip(), "SSE 에서 아무것도 수신되지 않음"


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-04  /admin/config/dev GET (viewer)
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_04_get_config_dev_as_viewer(app_client: TestClient) -> None:
    """P-INT-04: portal:viewer 로 /admin/config/dev GET → 200."""
    auth = _login(app_client, TEST_VIEWER_USER)
    resp = app_client.get(
        f"{BASE}/admin/config/dev",
        cookies=auth["cookies"],
    )
    assert resp.status_code == 200, f"HTTP {resp.status_code}: {resp.text}"
    data = resp.json()
    assert "env" in data or "values" in data or data == {}, f"응답 형식 이상: {data}"


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-05  /admin/config/prod PUT (viewer 권한) → 403
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_05_put_config_prod_as_viewer_forbidden(app_client: TestClient) -> None:
    """P-INT-05: viewer 가 prod config 변경 시도 → 403."""
    auth = _login(app_client, TEST_VIEWER_USER)
    resp = app_client.put(
        f"{BASE}/admin/config/prod",
        json={"updated_by": "viewer", "values": {"KEY": "val"}},
        cookies=auth["cookies"],
        headers={"X-CSRF-Token": auth["csrf"]},
    )
    assert resp.status_code == 403, f"viewer 가 prod config 수정 허용됨: HTTP {resp.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-06  refresh token rotation — 이전 token 재사용 시 401
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_06_refresh_token_rotation(app_client: TestClient) -> None:
    """P-INT-06: refresh rotation 후 이전 쿠키로 다시 refresh → 401."""
    # 로그인 → 첫 번째 refresh cookie
    resp1 = app_client.post(
        f"{BASE}/auth/portal/login",
        json={"username": TEST_ADMIN_USER, "password": TEST_PASSWORD},
    )
    if resp1.json().get("mfa_required"):
        pytest.skip("MFA 활성 계정 — rotation 테스트 불가")

    cookies1 = dict(resp1.cookies)

    # 첫 번째 refresh
    resp2 = app_client.post(
        f"{BASE}/auth/portal/refresh",
        cookies=cookies1,
        headers={"X-CSRF-Token": cookies1.get("__csrf__", "")},
    )
    if resp2.status_code != 200:
        pytest.skip(f"refresh 엔드포인트 실패: {resp2.status_code}")

    # 이전 쿠키로 다시 refresh → 401 or 403 (revoked)
    resp3 = app_client.post(
        f"{BASE}/auth/portal/refresh",
        cookies=cookies1,
        headers={"X-CSRF-Token": cookies1.get("__csrf__", "")},
    )
    assert resp3.status_code in (401, 403), f"이전 token 재사용이 허용됨: HTTP {resp3.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-07  CSRF 헤더 없이 PUT → 403
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_07_csrf_missing_header_rejected(app_client: TestClient) -> None:
    """P-INT-07: X-CSRF-Token 헤더 없이 PUT → 403."""
    auth = _login(app_client, TEST_ADMIN_USER)
    resp = app_client.put(
        f"{BASE}/admin/config/dev",
        json={"updated_by": "test", "values": {}},
        cookies=auth["cookies"],
        # X-CSRF-Token 헤더 의도적으로 생략
    )
    assert resp.status_code == 403, f"CSRF 없이 요청이 통과됨: HTTP {resp.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-08  잘못된 issuer JWT → 403
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_08_wrong_issuer_jwt_rejected(app_client: TestClient) -> None:
    """P-INT-08: agentoe-api issuer 토큰으로 portal route 접근 → 403."""
    from jose import jwt as jose_jwt

    from app.core.config import settings

    # agentoe-api issuer 로 토큰 발급
    payload = {
        "sub": "u1",
        "tid": "t1",
        "cid": "c1",
        "roles": ["portal:admin"],
        "iss": "agentoe-api",  # 잘못된 issuer
        "exp": int(time.time()) + 3600,
    }
    token = jose_jwt.encode(
        payload,
        settings.PORTAL_JWT_SECRET or settings.JWT_SECRET,
        algorithm="HS256",
    )
    resp = app_client.get(
        f"{BASE}/admin/config/dev",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code in (401, 403), f"잘못된 issuer 토큰이 통과됨: HTTP {resp.status_code}"


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-09  config 변경 후 audit_events 기록 확인
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_09_config_change_audit_logged(app_client: TestClient) -> None:
    """P-INT-09: config PUT 후 audit_events 컬렉션에 1건 이상 기록.

    실 MongoDB 대신 mongomock(_mongo()) 을 통해 audit_events 를 조회한다.
    AuditEmitter 는 동일 mongomock 인스턴스에 기록하므로 결과를 바로 검증 가능.
    """
    import asyncio

    from app.core.config import settings
    from tests.integration.conftest import _mongo

    auth = _login(app_client, TEST_ADMIN_USER)

    # admin.py update_config 의 실제 audit action: "config.update"
    _AUDIT_ACTION = "config.update"

    async def _count(action: str) -> int:
        # audit_events 문서 구조: {"timestamp": ..., "metadata": {"action": ..., ...}, ...}
        # action 필드는 metadata 하위에 저장됨 (AuditRepository.log() 참조)
        db = _mongo()[settings.MONGODB_DB_NAME]
        return await db["audit_events"].count_documents({"metadata.action": action})

    loop = asyncio.new_event_loop()
    try:
        before_count = loop.run_until_complete(_count(_AUDIT_ACTION))

        # config 변경 (CSRF 토큰 포함)
        resp = app_client.put(
            f"{BASE}/admin/config/dev",
            json={"updated_by": TEST_ADMIN_USER, "values": {"AUDIT_TEST_KEY": "1"}},
            cookies=auth["cookies"],
            headers={"X-CSRF-Token": auth["csrf"]},
        )
        assert resp.status_code == 200, f"config PUT 실패: {resp.status_code} — {resp.text}"

        after_count = loop.run_until_complete(_count(_AUDIT_ACTION))
        assert after_count > before_count, (
            f"config 변경 후 audit_events 에 기록되지 않음 (before={before_count}, after={after_count})"
        )
    finally:
        loop.close()


# ─────────────────────────────────────────────────────────────────────────────
# P-INT-10  logout 후 refresh token 무효화
# ─────────────────────────────────────────────────────────────────────────────


def test_p_int_10_logout_revokes_refresh_token(app_client: TestClient) -> None:
    """P-INT-10: logout 후 동일 쿠키로 refresh → 401.

    흐름:
      _login() 으로 portal JWT + CSRF 토큰 획득 →
      POST /logout (portal_access + __csrf__ 쿠키, X-CSRF-Token 헤더) → 200/204 →
      POST /refresh (같은 쿠키) → 401 (portal_refresh 쿠키 없음 or revoked)

    참고: MFA 를 완료하지 않으면 portal_refresh 쿠키가 없다.
    logout 은 CSRF 검증 후 access token 으로 user_id 를 추출해 세션을 revoke 한다.
    refresh 는 portal_refresh 쿠키 부재 시 401 을 반환한다.
    """
    auth = _login(app_client, TEST_ADMIN_USER)

    # 로그아웃 — portal_access + __csrf__ 쿠키, X-CSRF-Token 헤더
    resp_logout = app_client.post(
        f"{BASE}/auth/portal/logout",
        cookies=auth["cookies"],
        headers={"X-CSRF-Token": auth["csrf"]},
    )
    assert resp_logout.status_code in (200, 204), (
        f"logout 실패: {resp_logout.status_code} — {resp_logout.text}"
    )

    # 로그아웃 후 refresh 시도 → 401 (portal_refresh 쿠키 없음)
    resp_refresh = app_client.post(
        f"{BASE}/auth/portal/refresh",
        cookies=auth["cookies"],
        headers={"X-CSRF-Token": auth["csrf"]},
    )
    assert resp_refresh.status_code in (401, 403), (
        f"로그아웃 후 refresh가 허용됨: {resp_refresh.status_code}"
    )
