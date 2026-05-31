/**
 * 상담 이력 & 대화 턴 뷰어 (Phase N — N2.4)
 *
 * 변경:
 *   - getSessions API 시그니처 업데이트 (params 객체 방식).
 *   - getSessionTurns 연동 — 세션 클릭 시 대화 턴 로드.
 *   - useSSE(SESSIONS_ACTIVE) — 신규/종료 세션 실시간 반영 (라이브 배지).
 *   - 상태 필터 (active/completed/failed/transferred) + 테넌트 필터 UI 추가.
 */
import { useCallback, useEffect, useState } from "react";
import {
  getSessions, getSessionTurns,
  type SessionSummary, type TurnItem,
} from "../lib/api";
import { useSSE } from "../providers/SSEProvider";
import { SSE_CHANNELS } from "../lib/sse";

// ── 상수 / 헬퍼 ──────────────────────────────────────────────────────────────
const PAGE_SIZE = 20;

const STATUS_COLOR: Record<string, string> = {
  completed: "green", failed: "red", transferred: "blue", active: "yellow",
};
const STATUS_LABEL: Record<string, string> = {
  completed: "완료", failed: "실패", transferred: "전환", active: "통화 중",
};

function fmtDur(s: number | null) {
  if (s == null) return "—";
  const m = Math.floor(s / 60), sec = Math.round(s % 60);
  return m ? `${m}분 ${sec}초` : `${sec}초`;
}

// ── LiveBadge ─────────────────────────────────────────────────────────────────
function LiveBadge({ live }: { live: boolean }) {
  return live ? (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 4,
      fontSize: 10, color: "#22c55e", letterSpacing: "0.05em",
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: "50%",
        background: "#22c55e", boxShadow: "0 0 5px #22c55e",
      }} />
      LIVE
    </span>
  ) : null;
}

// ── TurnsPanel ────────────────────────────────────────────────────────────────
function TurnsPanel({
  session, onClose,
}: {
  session: SessionSummary;
  onClose: () => void;
}) {
  const [turns, setTurns] = useState<TurnItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [offset, setOffset] = useState(0);
  const [total, setTotal] = useState(0);

  const loadTurns = useCallback(async (off: number) => {
    setLoading(true);
    try {
      const r = await getSessionTurns(session.session_id, { limit: 50, offset: off });
      setTurns(r.items);
      setTotal(r.total);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, [session.session_id]);

  useEffect(() => { void loadTurns(0); setOffset(0); }, [loadTurns]);

  return (
    <div style={{
      flex: "0 0 420px", overflow: "auto",
      borderLeft: "1px solid var(--border)", padding: "16px 14px",
      display: "flex", flexDirection: "column", gap: 0,
    }}>
      {/* 헤더 */}
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", marginBottom: 12 }}>
        <div>
          <div style={{ fontSize: 13, fontWeight: 700, fontFamily: "monospace" }}>
            {session.session_id.slice(-12)}
          </div>
          <div style={{ fontSize: 11, color: "var(--text-3)", marginTop: 2 }}>
            {session.caller_number} · {fmtDur(session.duration_s)} · {session.turn_count}턴
            {session.trace_id && (
              <span style={{ marginLeft: 6, color: "#3b82f6" }}>
                trace: {session.trace_id.slice(0, 8)}
              </span>
            )}
          </div>
        </div>
        <button
          className="btn btn-ghost btn-sm"
          onClick={onClose}
          style={{ fontSize: 16, padding: "0 6px" }}
        >
          ✕
        </button>
      </div>

      {/* 상태 배지 */}
      <div style={{ marginBottom: 10 }}>
        <span className={`badge ${STATUS_COLOR[session.status]}`}>
          {STATUS_LABEL[session.status]}
        </span>
        {session.error_count > 0 && (
          <span style={{ marginLeft: 6, fontSize: 11, color: "#ef4444" }}>
            에러 {session.error_count}건
          </span>
        )}
      </div>

      <div className="card-title" style={{ marginBottom: 8 }}>
        대화 내용 (총 {total}턴)
      </div>

      {/* 턴 목록 */}
      {loading ? (
        <div className="spinner" />
      ) : (
        <>
          <div style={{ flex: 1, overflow: "auto" }}>
            {turns.map((t) => (
              <div key={`${t.turn}-${t.role}`} style={{
                padding: "8px 10px", marginBottom: 6,
                background: t.role === "bot" ? "var(--bg-3)" : "var(--blue-glow)",
                borderRadius: 6,
                borderLeft: `3px solid ${t.role === "bot" ? "var(--text-3)" : "var(--blue)"}`,
              }}>
                <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 3 }}>
                  <span style={{ fontSize: 10, color: "var(--text-3)" }}>
                    {t.role === "bot" ? "🤖 봇" : "👤 고객"} #{t.turn}
                  </span>
                  <span style={{ fontSize: 10, color: "var(--text-3)" }}>
                    {t.ts.slice(11, 19)}
                    {t.latency_ms != null && (
                      <span style={{ marginLeft: 4, color: t.latency_ms > 1200 ? "#ef4444" : "#64748b" }}>
                        {t.latency_ms}ms
                      </span>
                    )}
                  </span>
                </div>
                <div style={{ fontSize: 12, color: "var(--text)", lineHeight: 1.5 }}>{t.text}</div>
              </div>
            ))}
          </div>

          {/* 페이지네이션 */}
          {total > 50 && (
            <div style={{ display: "flex", gap: 6, marginTop: 10, justifyContent: "center" }}>
              <button
                className="btn btn-ghost btn-sm"
                disabled={offset === 0}
                onClick={() => { const o = Math.max(0, offset - 50); setOffset(o); void loadTurns(o); }}
              >
                ← 이전
              </button>
              <span style={{ fontSize: 11, color: "var(--text-3)", alignSelf: "center" }}>
                {offset + 1}–{Math.min(offset + 50, total)} / {total}
              </span>
              <button
                className="btn btn-ghost btn-sm"
                disabled={offset + 50 >= total}
                onClick={() => { const o = offset + 50; setOffset(o); void loadTurns(o); }}
              >
                다음 →
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

// ── Sessions ──────────────────────────────────────────────────────────────────
export default function Sessions() {
  const [sessions, setSessions]   = useState<SessionSummary[]>([]);
  const [total, setTotal]         = useState(0);
  const [page, setPage]           = useState(1);
  const [statusFilter, setStatus] = useState<string>("");
  const [tenantFilter, setTenant] = useState("");
  const [selected, setSelected]   = useState<SessionSummary | null>(null);
  const [loading, setLoading]     = useState(true);
  const [liveCount, setLiveCount] = useState(0); // SSE 로 수신한 신규 이벤트 카운트

  // SSE — sessions.active 채널 구독
  const sseMsg = useSSE("SESSIONS_ACTIVE");
  const [sseActive, setSseActive] = useState(false);

  // SSE 이벤트 → 리스트 실시간 갱신
  useEffect(() => {
    if (!sseMsg) return;
    setSseActive(true);
    try {
      const evt = JSON.parse(sseMsg.data) as { type?: string; session?: SessionSummary };
      if (!evt.session) return;
      const incoming = evt.session;
      setLiveCount(c => c + 1);

      setSessions(prev => {
        const idx = prev.findIndex(s => s.session_id === incoming.session_id);
        if (idx >= 0) {
          // 기존 세션 업데이트
          const updated = [...prev];
          updated[idx] = incoming;
          return updated;
        }
        // 신규 — 맨 위에 추가, 초과분 제거
        return [incoming, ...prev].slice(0, PAGE_SIZE);
      });
    } catch { /* heartbeat 등 무시 */ }
  }, [sseMsg]);

  // 목록 로드
  const load = useCallback(async (pg: number, status: string, tenant: string) => {
    setLoading(true);
    try {
      const r = await getSessions({
        limit: PAGE_SIZE,
        offset: (pg - 1) * PAGE_SIZE,
        ...(status ? { status: status as SessionSummary["status"] } : {}),
        ...(tenant ? { tenant_id: tenant } : {}),
      });
      setSessions(r.items);
      setTotal(r.total);
      setLiveCount(0);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(page, statusFilter, tenantFilter); }, [load, page, statusFilter, tenantFilter]);

  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  return (
    <div>
      {/* 헤더 */}
      <div className="page-header">
        <div>
          <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            상담 이력
            <LiveBadge live={sseActive} />
            {liveCount > 0 && (
              <span style={{
                fontSize: 10, background: "var(--blue-glow)", color: "var(--blue)",
                padding: "1px 6px", borderRadius: 10, border: "1px solid var(--blue)",
              }}>
                +{liveCount} 신규
              </span>
            )}
          </div>
          <div className="page-sub">총 {total}건 · 페이지 {page}/{totalPages}</div>
        </div>

        {/* 필터 + 페이지네이션 */}
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <select
            className="select-sm"
            value={statusFilter}
            onChange={e => { setStatus(e.target.value); setPage(1); }}
            style={{ fontSize: 12, padding: "4px 8px", background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)" }}
          >
            <option value="">전체 상태</option>
            <option value="active">통화 중</option>
            <option value="completed">완료</option>
            <option value="failed">실패</option>
            <option value="transferred">전환</option>
          </select>
          <input
            placeholder="테넌트 ID"
            value={tenantFilter}
            onChange={e => { setTenant(e.target.value); setPage(1); }}
            style={{ fontSize: 12, padding: "4px 8px", width: 120, background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)" }}
          />
          <button className="btn btn-ghost btn-sm" onClick={() => void load(page, statusFilter, tenantFilter)}>
            ↻
          </button>
          <button className="btn btn-ghost btn-sm" disabled={page <= 1}       onClick={() => setPage(p => p - 1)}>←</button>
          <button className="btn btn-ghost btn-sm" disabled={page >= totalPages} onClick={() => setPage(p => p + 1)}>→</button>
        </div>
      </div>

      {/* 본문: 목록 + 패널 */}
      <div style={{
        display: "flex", gap: 0,
        background: "var(--bg-2)", border: "1px solid var(--border)",
        borderRadius: "var(--radius)", overflow: "hidden", minHeight: 600,
      }}>
        {/* 세션 목록 */}
        <div style={{ flex: 1, overflow: "auto" }}>
          {loading ? <div className="spinner" /> : (
            <table className="data-table">
              <thead>
                <tr>
                  <th>세션 ID</th>
                  <th>테넌트</th>
                  <th>시나리오</th>
                  <th>발신 번호</th>
                  <th>시작 시간</th>
                  <th>통화 시간</th>
                  <th>턴</th>
                  <th>에러</th>
                  <th>상태</th>
                </tr>
              </thead>
              <tbody>
                {sessions.map((s) => (
                  <tr
                    key={s.session_id}
                    onClick={() => setSelected(prev => prev?.session_id === s.session_id ? null : s)}
                    style={{
                      background: selected?.session_id === s.session_id ? "var(--blue-glow)" : "",
                      cursor: "pointer",
                    }}
                  >
                    <td className="mono" style={{ fontSize: 11 }}>{s.session_id.slice(-12)}</td>
                    <td style={{ fontSize: 11, color: "var(--text-2)" }}>{s.tenant_id}</td>
                    <td style={{ fontSize: 11 }}>{s.scenario_id}</td>
                    <td className="mono" style={{ fontSize: 11 }}>{s.caller_number}</td>
                    <td style={{ fontSize: 11, color: "var(--text-2)" }}>{s.started_at.slice(11, 19)}</td>
                    <td style={{ fontSize: 12 }}>{fmtDur(s.duration_s)}</td>
                    <td style={{ textAlign: "center", fontSize: 12 }}>{s.turn_count}</td>
                    <td style={{ textAlign: "center", fontSize: 12, color: s.error_count > 0 ? "#ef4444" : "var(--text-3)" }}>
                      {s.error_count > 0 ? s.error_count : "—"}
                    </td>
                    <td>
                      <span className={`badge ${STATUS_COLOR[s.status]}`}>
                        {STATUS_LABEL[s.status]}
                        {s.status === "active" && (
                          <span style={{
                            display: "inline-block", width: 5, height: 5, borderRadius: "50%",
                            background: "#22c55e", marginLeft: 4, boxShadow: "0 0 4px #22c55e",
                          }} />
                        )}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </div>

        {/* 대화 턴 패널 */}
        {selected && (
          <TurnsPanel session={selected} onClose={() => setSelected(null)} />
        )}
      </div>
    </div>
  );
}
