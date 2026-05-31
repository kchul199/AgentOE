/**
 * AuthProvider — 로그인 상태 관리 + 자동 토큰 갱신 (Phase N — N1.11).
 *
 * 흐름:
 *   1. 마운트 시 /refresh 를 silent probe (쿠키가 살아있으면 roles 확인).
 *   2. 성공 → authenticated. 실패 → unauthenticated (로그인 화면).
 *   3. access 쿠키 만료 전(1분) 자동 refresh. 실패 시 logout().
 *
 * 주의: access JWT 가 HttpOnly 쿠키라 JS 에서 읽지 않음.
 *       roles 는 MFA verify 응답 JSON 에서 받아 메모리에만 보관.
 */

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  portalLogin,
  portalLogout,
  portalMfaVerify,
  portalRefresh,
  type LoginResult,
  type MfaVerifyResult,
  type PortalRole,
} from "../lib/auth";

// ── 타입 ──────────────────────────────────────────────────────────────────

export type AuthStatus = "loading" | "unauthenticated" | "mfa_challenge" | "authenticated";

export interface AuthState {
  status: AuthStatus;
  roles: PortalRole[];
  /** 로그인 username (N5.1 config updated_by 등에서 사용) */
  username: string | null;
  /** MFA 1단계 통과 후 challenge_token 임시 보관 */
  challengeToken: string | null;
  /** MFA 아직 등록 안 된 사용자 */
  mfaEnrollRequired: boolean;
}

export interface AuthContextValue extends AuthState {
  login: (username: string, password: string) => Promise<LoginResult>;
  verifyMfa: (code: string) => Promise<void>;
  logout: () => Promise<void>;
  hasRole: (role: PortalRole) => boolean;
}

// ── context ────────────────────────────────────────────────────────────────

const AuthContext = createContext<AuthContextValue | null>(null);

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}

// ── provider ───────────────────────────────────────────────────────────────

const PORTAL_JWT_EXPIRE_MS = 15 * 60 * 1000; // 15분 (서버와 동일)
const REFRESH_BEFORE_MS    = 60 * 1000;       // 만료 1분 전 갱신

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({
    status: "loading",
    roles: [],
    username: null,
    challengeToken: null,
    mfaEnrollRequired: false,
  });

  const refreshTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // ── 갱신 스케줄러 ───────────────────────────────────────────────────────

  const scheduleRefresh = useCallback(() => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    const delay = PORTAL_JWT_EXPIRE_MS - REFRESH_BEFORE_MS;
    refreshTimer.current = setTimeout(async () => {
      const ok = await portalRefresh();
      if (ok) {
        scheduleRefresh(); // 다음 사이클 예약
      } else {
        // refresh 실패 → 강제 로그아웃
        setState({ status: "unauthenticated", roles: [], username: null, challengeToken: null, mfaEnrollRequired: false });
      }
    }, delay);
  }, []);

  // ── silent probe (마운트 시) ──────────────────────────────────────────────

  useEffect(() => {
    let cancelled = false;
    (async () => {
      const ok = await portalRefresh();
      if (cancelled) return;
      if (ok) {
        // refresh 성공했지만 roles 를 모름 → viewer 로 fallback (SSR-safe)
        // 실제 roles 는 다음 MFA verify 에서 채워짐; 여기선 re-auth 흐름 사용
        // silent probe 로 쿠키 살아있으면 "authenticated" 전환만.
        setState({ status: "authenticated", roles: [], username: null, challengeToken: null, mfaEnrollRequired: false });
        scheduleRefresh();
      } else {
        setState({ status: "unauthenticated", roles: [], username: null, challengeToken: null, mfaEnrollRequired: false });
      }
    })();
    return () => { cancelled = true; };
  }, [scheduleRefresh]);

  // ── unmount 정리 ─────────────────────────────────────────────────────────

  useEffect(() => {
    return () => {
      if (refreshTimer.current) clearTimeout(refreshTimer.current);
    };
  }, []);

  // ── actions ───────────────────────────────────────────────────────────────

  const login = useCallback(async (username: string, password: string): Promise<LoginResult> => {
    const result = await portalLogin(username, password);
    if (result.mfa_required) {
      setState((prev) => ({
        ...prev,
        status: "mfa_challenge",
        username,                               // N5.1: 로그인 시 username 저장
        challengeToken: result.challenge_token ?? null,
        mfaEnrollRequired: result.mfa_enrolled === false,
      }));
    } else {
      // MFA 없는 경우 (dev 전용 설정 가정)
      setState((prev) => ({ ...prev, status: "authenticated", username }));
      scheduleRefresh();
    }
    return result;
  }, [scheduleRefresh]);

  const verifyMfa = useCallback(async (code: string): Promise<void> => {
    const { challengeToken } = state;
    if (!challengeToken) throw new Error("No challenge token — call login() first");
    const result: MfaVerifyResult = await portalMfaVerify(challengeToken, code);
    setState((prev) => ({
      ...prev,
      status: "authenticated",
      roles: result.roles as PortalRole[],
      challengeToken: null,
      mfaEnrollRequired: false,
    }));
    scheduleRefresh();
  }, [state, scheduleRefresh]);

  const logout = useCallback(async (): Promise<void> => {
    if (refreshTimer.current) clearTimeout(refreshTimer.current);
    try { await portalLogout(); } catch { /* ignore */ }
    setState({ status: "unauthenticated", roles: [], username: null, challengeToken: null, mfaEnrollRequired: false });
  }, []);

  const hasRole = useCallback((role: PortalRole): boolean => {
    return state.roles.includes(role) || state.roles.includes("portal:admin");
  }, [state.roles]);

  const value: AuthContextValue = { ...state, login, verifyMfa, logout, hasRole };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
