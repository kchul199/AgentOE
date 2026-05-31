/**
 * LoginPage — username/password → MFA TOTP → 대시보드 진입 (Phase N — N1.11).
 *
 * 화면 상태:
 *   credentials  → username/password 입력
 *   mfa          → TOTP 6자리 입력
 *   enroll       → MFA 미등록 사용자: QR 코드 표시 후 TOTP 입력
 */

import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../providers/AuthProvider";
import { portalMfaEnroll } from "../../lib/auth";

type Step = "credentials" | "mfa" | "enroll";

export default function LoginPage() {
  const { login, verifyMfa, status, mfaEnrollRequired } = useAuth();

  const [step, setStep] = useState<Step>("credentials");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode]     = useState("");
  const [error, setError]   = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [totpUri, setTotpUri] = useState<string | null>(null);

  const codeRef = useRef<HTMLInputElement>(null);

  // MFA 입력 화면으로 전환되면 input focus
  useEffect(() => {
    if (step === "mfa" || step === "enroll") {
      setTimeout(() => codeRef.current?.focus(), 100);
    }
  }, [step]);

  // 인증 완료되면 App 에서 redirect 처리 (status === "authenticated")
  // 여기서는 step 전환만 담당

  // ── 1단계: credentials ────────────────────────────────────────────────────

  const handleCredentials = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password.trim()) return;
    setError(null);
    setLoading(true);
    try {
      const result = await login(username, password);
      if (result.mfa_required) {
        if (result.mfa_enrolled === false) {
          // MFA 미등록 → enroll flow
          await triggerEnroll();
        } else {
          setStep("mfa");
        }
      }
      // mfa_required === false 면 AuthProvider 가 authenticated 로 전환
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "로그인 실패");
    } finally {
      setLoading(false);
    }
  };

  const triggerEnroll = async () => {
    try {
      const { totp_uri } = await portalMfaEnroll();
      setTotpUri(totp_uri);
      setStep("enroll");
    } catch {
      setError("MFA 등록 초기화 실패 — 관리자에게 문의하세요");
    }
  };

  // ── 2단계: MFA 코드 검증 ──────────────────────────────────────────────────

  const handleMfa = async (e: React.FormEvent) => {
    e.preventDefault();
    if (code.length < 6) return;
    setError(null);
    setLoading(true);
    try {
      await verifyMfa(code);
      // verifyMfa 성공 → status "authenticated" → App 이 리다이렉트
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "인증 코드 오류");
      setCode("");
    } finally {
      setLoading(false);
    }
  };

  // code 6자리 자동 submit (mfa/enroll 공통)
  const handleCodeChange = (val: string) => {
    const digits = val.replace(/\D/g, "").slice(0, 6);
    setCode(digits);
  };

  // ── 렌더 ──────────────────────────────────────────────────────────────────

  return (
    <div style={styles.root}>
      <div style={styles.card}>
        {/* 헤더 */}
        <div style={styles.header}>
          <div style={styles.brandIcon}>O</div>
          <div style={styles.brandName}>AgentOE 운영포탈</div>
          <div style={styles.brandSub}>
            {step === "credentials" && "관리자 로그인"}
            {step === "mfa"        && "MFA 인증"}
            {step === "enroll"     && "MFA 최초 등록"}
          </div>
        </div>

        {/* 에러 배너 */}
        {error && (
          <div style={styles.errorBanner} role="alert">
            {error}
          </div>
        )}

        {/* ── Step 1: credentials ── */}
        {step === "credentials" && (
          <form onSubmit={handleCredentials} style={styles.form}>
            <label style={styles.label}>
              사용자 ID
              <input
                style={styles.input}
                type="text"
                autoComplete="username"
                value={username}
                onChange={(e) => setUsername(e.target.value)}
                disabled={loading}
                required
              />
            </label>
            <label style={styles.label}>
              비밀번호
              <input
                style={styles.input}
                type="password"
                autoComplete="current-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                disabled={loading}
                required
              />
            </label>
            <button style={styles.btn} type="submit" disabled={loading}>
              {loading ? "로그인 중…" : "로그인"}
            </button>
          </form>
        )}

        {/* ── Step 2: MFA ── */}
        {step === "mfa" && (
          <form onSubmit={handleMfa} style={styles.form}>
            <p style={styles.hint}>
              인증 앱(Google Authenticator 등)의 6자리 코드를 입력하세요.
            </p>
            <input
              ref={codeRef}
              style={{ ...styles.input, textAlign: "center", fontSize: "1.5rem", letterSpacing: "0.4em" }}
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={(e) => handleCodeChange(e.target.value)}
              disabled={loading}
              placeholder="000000"
            />
            <button style={styles.btn} type="submit" disabled={loading || code.length < 6}>
              {loading ? "검증 중…" : "확인"}
            </button>
            <button
              type="button"
              style={styles.linkBtn}
              onClick={() => { setStep("credentials"); setCode(""); setError(null); }}
            >
              ← 이전
            </button>
          </form>
        )}

        {/* ── Step 3: MFA Enroll ── */}
        {step === "enroll" && (
          <form onSubmit={handleMfa} style={styles.form}>
            <p style={styles.hint}>
              아직 MFA 가 등록되어 있지 않습니다.<br />
              인증 앱으로 아래 QR 코드를 스캔한 뒤 6자리 코드를 입력하세요.
            </p>
            {totpUri && (
              <div style={styles.qrWrap}>
                {/* QR 렌더: totp_uri 를 img src 로 변환 — Google Charts API */}
                <img
                  alt="TOTP QR 코드"
                  style={styles.qrImg}
                  src={`https://chart.googleapis.com/chart?chs=200x200&chld=M|0&cht=qr&chl=${encodeURIComponent(totpUri)}`}
                />
                <div style={styles.qrSecret}>
                  URI: <code style={{ fontSize: "0.65rem", wordBreak: "break-all" }}>{totpUri}</code>
                </div>
              </div>
            )}
            <input
              ref={codeRef}
              style={{ ...styles.input, textAlign: "center", fontSize: "1.5rem", letterSpacing: "0.4em" }}
              type="text"
              inputMode="numeric"
              maxLength={6}
              value={code}
              onChange={(e) => handleCodeChange(e.target.value)}
              disabled={loading}
              placeholder="000000"
            />
            <button style={styles.btn} type="submit" disabled={loading || code.length < 6}>
              {loading ? "등록 중…" : "등록 완료"}
            </button>
          </form>
        )}
      </div>
    </div>
  );
}

// ── 인라인 스타일 (기존 app.css 변수 참조) ───────────────────────────────────

const styles = {
  root: {
    minHeight: "100vh",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    background: "var(--bg-base, #0f1117)",
  } as React.CSSProperties,

  card: {
    width: 360,
    background: "var(--bg-card, #1a1d27)",
    border: "1px solid var(--border, #2a2d3a)",
    borderRadius: 12,
    padding: "2rem",
    boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
  } as React.CSSProperties,

  header: {
    textAlign: "center" as const,
    marginBottom: "1.5rem",
  } as React.CSSProperties,

  brandIcon: {
    width: 48, height: 48,
    borderRadius: 12,
    background: "var(--accent, #6366f1)",
    display: "flex", alignItems: "center", justifyContent: "center",
    fontSize: "1.5rem", fontWeight: 700,
    margin: "0 auto 0.75rem",
    color: "#fff",
  } as React.CSSProperties,

  brandName: {
    fontSize: "1.1rem",
    fontWeight: 700,
    color: "var(--text-primary, #e2e8f0)",
  } as React.CSSProperties,

  brandSub: {
    fontSize: "0.8rem",
    color: "var(--text-muted, #94a3b8)",
    marginTop: 4,
  } as React.CSSProperties,

  errorBanner: {
    background: "rgba(239,68,68,0.15)",
    border: "1px solid rgba(239,68,68,0.4)",
    borderRadius: 6,
    padding: "0.5rem 0.75rem",
    marginBottom: "1rem",
    fontSize: "0.82rem",
    color: "#f87171",
  } as React.CSSProperties,

  form: {
    display: "flex",
    flexDirection: "column" as const,
    gap: "0.75rem",
  } as React.CSSProperties,

  label: {
    display: "flex",
    flexDirection: "column" as const,
    gap: 4,
    fontSize: "0.82rem",
    color: "var(--text-secondary, #cbd5e1)",
  } as React.CSSProperties,

  input: {
    background: "var(--bg-base, #0f1117)",
    border: "1px solid var(--border, #2a2d3a)",
    borderRadius: 6,
    padding: "0.6rem 0.75rem",
    color: "var(--text-primary, #e2e8f0)",
    fontSize: "0.9rem",
    outline: "none",
    width: "100%",
    boxSizing: "border-box" as const,
  } as React.CSSProperties,

  btn: {
    marginTop: "0.5rem",
    padding: "0.7rem",
    background: "var(--accent, #6366f1)",
    color: "#fff",
    border: "none",
    borderRadius: 6,
    fontWeight: 600,
    fontSize: "0.9rem",
    cursor: "pointer",
  } as React.CSSProperties,

  linkBtn: {
    background: "none",
    border: "none",
    color: "var(--text-muted, #94a3b8)",
    fontSize: "0.8rem",
    cursor: "pointer",
    padding: 0,
    textAlign: "left" as const,
  } as React.CSSProperties,

  hint: {
    fontSize: "0.82rem",
    color: "var(--text-secondary, #cbd5e1)",
    margin: 0,
    lineHeight: 1.5,
  } as React.CSSProperties,

  qrWrap: {
    textAlign: "center" as const,
    padding: "0.5rem",
  } as React.CSSProperties,

  qrImg: {
    width: 180, height: 180,
    borderRadius: 8,
    background: "#fff",
    padding: 8,
  } as React.CSSProperties,

  qrSecret: {
    marginTop: "0.5rem",
    fontSize: "0.7rem",
    color: "var(--text-muted, #94a3b8)",
    wordBreak: "break-all" as const,
  } as React.CSSProperties,
} as const;
