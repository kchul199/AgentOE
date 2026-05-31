/**
 * App — 루트 컴포넌트 (Phase N — N1.11).
 *
 * 인증 상태 guard:
 *   loading         → 전체화면 스피너
 *   unauthenticated / mfa_challenge → LoginPage
 *   authenticated   → 기존 포탈 레이아웃
 */

import { useState } from "react";
import { useAuth } from "./providers/AuthProvider";
import LoginPage   from "./pages/auth/LoginPage";
import Dashboard   from "./pages/Dashboard";
import Config      from "./pages/Config";
import Sessions    from "./pages/Sessions";
import KillSwitch  from "./pages/KillSwitch";
import Scenarios   from "./pages/Scenarios";
import AuditPage   from "./pages/AuditPage";
import Alerts      from "./pages/Alerts";

type Page = "dashboard" | "config" | "sessions" | "killswitch" | "scenarios" | "audit" | "alerts";

const NAV: { id: Page; icon: string; label: string; section?: string }[] = [
  { id: "dashboard",  icon: "◉", label: "모니터링 대시보드", section: "운영" },
  { id: "alerts",     icon: "🔔", label: "알람",             section: "" },
  { id: "killswitch", icon: "⚡", label: "Kill Switch",      section: "" },
  { id: "sessions",   icon: "📋", label: "상담 이력",         section: "" },
  { id: "audit",      icon: "🔍", label: "감사 로그",         section: "" },
  { id: "config",     icon: "⚙", label: "환경 설정",         section: "관리" },
  { id: "scenarios",  icon: "◈", label: "시나리오 관리",     section: "" },
];

const PAGE_TITLES: Record<Page, string> = {
  dashboard:  "모니터링 대시보드",
  config:     "환경별 설정 관리",
  sessions:   "상담 이력 & 로그",
  killswitch: "Kill Switch",
  scenarios:  "시나리오 관리",
  audit:      "감사 로그",
  alerts:     "알람 모니터링",
};

export default function App() {
  const { status, logout, roles } = useAuth();
  const [page, setPage] = useState<Page>("dashboard");

  // ── 인증 가드 ──────────────────────────────────────────────────────────────

  if (status === "loading") {
    return (
      <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: "100vh", background: "#0f1117", color: "#94a3b8", fontSize: "0.9rem" }}>
        인증 확인 중…
      </div>
    );
  }

  if (status === "unauthenticated" || status === "mfa_challenge") {
    return <LoginPage />;
  }

  // ── 포탈 레이아웃 (authenticated) ─────────────────────────────────────────

  const PageContent = (() => {
    switch (page) {
      case "dashboard":  return <Dashboard />;
      case "config":     return <Config />;
      case "sessions":   return <Sessions />;
      case "killswitch": return <KillSwitch />;
      case "scenarios":  return <Scenarios />;
      case "audit":      return <AuditPage />;
      case "alerts":     return <Alerts />;
    }
  })();

  // 역할 배지 (viewer/operator/admin)
  const roleLabel = roles.includes("portal:admin")    ? "admin"
                  : roles.includes("portal:operator") ? "operator"
                  : "viewer";

  return (
    <div className="ops-layout">
      {/* 사이드바 */}
      <aside className="ops-sidebar">
        <div className="sidebar-brand">
          <div className="brand-icon">O</div>
          <div>
            <div className="brand-name">AgentOE</div>
            <div className="brand-sub">통합 운영 포탈</div>
          </div>
        </div>

        <nav className="sidebar-nav">
          {NAV.map((item, idx) => (
            <>
              {item.section !== undefined && item.section !== "" && (
                <div key={`sec-${idx}`} className="nav-section-label">{item.section}</div>
              )}
              <button
                key={item.id}
                className={`nav-item ${page === item.id ? "active" : ""}`}
                onClick={() => setPage(item.id)}
              >
                <span className="nav-icon">{item.icon}</span>
                {item.label}
              </button>
            </>
          ))}
        </nav>

        <div className="sidebar-footer">AgentOE Ops Portal v1.0</div>
      </aside>

      {/* 헤더 */}
      <header className="ops-header">
        <div className="ops-header-title">{PAGE_TITLES[page]}</div>
        <span className="env-badge prod">
          <span className="header-dot" />
          상용 환경
        </span>
        <div className="header-user">
          <div className="user-avatar">{roleLabel[0].toUpperCase()}</div>
          <span style={{ fontSize: "0.8rem", color: "var(--text-secondary, #cbd5e1)" }}>
            {roleLabel}
          </span>
          <button
            onClick={logout}
            style={{ background: "none", border: "none", color: "var(--text-muted, #94a3b8)", cursor: "pointer", fontSize: "0.75rem", padding: "0 0.5rem" }}
            title="로그아웃"
          >
            로그아웃
          </button>
        </div>
      </header>

      {/* 메인 */}
      <main className="ops-main">{PageContent}</main>
    </div>
  );
}
