/**
 * Portal 인증 클라이언트 (Phase N — N1.11).
 *
 * 엔드포인트 (backend auth_portal.py):
 *   POST /api/v1/auth/portal/login
 *   POST /api/v1/auth/portal/mfa/verify
 *   POST /api/v1/auth/portal/refresh
 *   POST /api/v1/auth/portal/logout
 *   POST /api/v1/auth/portal/mfa/enroll
 *
 * 보안:
 *   - access/refresh JWT 는 HttpOnly 쿠키 — JS 에서 직접 읽지 않음.
 *   - CSRF: POST 에 X-CSRF-Token 헤더 자동 주입 (csrf.ts).
 *   - 401 응답 → refresh → 재시도 1회. 재시도도 실패 시 로그아웃.
 */

import { csrfHeaders } from "./csrf";

const PORTAL_BASE = "/api/v1/auth/portal";

// ── types ─────────────────────────────────────────────────────────────────

export interface LoginResult {
  mfa_required: boolean;
  mfa_enrolled?: boolean;
  challenge_token?: string;
  message?: string;
}

export interface MfaVerifyResult {
  ok: boolean;
  roles: string[];
}

export type PortalRole = "portal:viewer" | "portal:operator" | "portal:admin";

// ── helpers ───────────────────────────────────────────────────────────────

async function portalFetch(
  path: string,
  init: RequestInit = {},
  challengeToken?: string,
): Promise<Response> {
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...csrfHeaders(),
    ...(init.headers as Record<string, string> | undefined),
  };
  if (challengeToken) {
    headers["Authorization"] = `Bearer ${challengeToken}`;
  }
  return fetch(`${PORTAL_BASE}${path}`, {
    credentials: "include",  // HttpOnly 쿠키 전송
    ...init,
    headers,
  });
}

// ── public API ────────────────────────────────────────────────────────────

/** 1단계: username/password → MFA challenge token */
export async function portalLogin(
  username: string,
  password: string,
): Promise<LoginResult> {
  const res = await portalFetch("/login", {
    method: "POST",
    body: JSON.stringify({ username, password }),
  });
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `Login failed (${res.status})`);
  }
  return res.json() as Promise<LoginResult>;
}

/** 2단계: TOTP 코드 → access + refresh 쿠키 발급 */
export async function portalMfaVerify(
  challengeToken: string,
  code: string,
): Promise<MfaVerifyResult> {
  const res = await portalFetch(
    "/mfa/verify",
    { method: "POST", body: JSON.stringify({ code }) },
    challengeToken,
  );
  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(err.detail ?? `MFA verify failed (${res.status})`);
  }
  return res.json() as Promise<MfaVerifyResult>;
}

/** Refresh token rotation → 새 access 쿠키 */
export async function portalRefresh(): Promise<boolean> {
  const res = await portalFetch("/refresh", { method: "POST" });
  return res.ok;
}

/** 로그아웃 — 쿠키 삭제 + 서버 세션 revoke */
export async function portalLogout(): Promise<void> {
  await portalFetch("/logout", { method: "POST" });
}

/** TOTP 신규 등록 → QR URI 반환 */
export async function portalMfaEnroll(): Promise<{ totp_uri: string; secret: string }> {
  const res = await portalFetch("/mfa/enroll", { method: "POST" });
  if (!res.ok) throw new Error(`MFA enroll failed (${res.status})`);
  return res.json();
}
