/**
 * 감사 로그 실시간 뷰어 (Phase N — N2.5)
 *
 * - useSSE(AUDIT_TAIL): Redis pub agentoe:events:audit → audit.append SSE push.
 * - 무한 스크롤 방식 (최근 500건 인메모리 유지, 초과 시 오래된 것 드롭).
 * - 필터: action / actor / env.
 * - trace_id 클릭 → 드릴다운 모달 (세션 ID + trace 링크).
 * - RBAC: portal:operator+ only (backend 에서 강제, UI 는 operator/admin 표시).
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSSE } from "../providers/SSEProvider";
import { SSE_CHANNELS } from "../lib/sse";

// ── 타입 ─────────────────────────────────────────────────────────────────────
interface AuditEvent {
  id:         string;   // 로컬 uuid (SSE seq)
  ts:         string;
  action:     string;   // e.g. "tenant.create", "alert.silence_create"
  actor_id:   string;
  actor_email?: string;
  env:        string;
  resource_type?: string;
  resource_id?: string;
  trace_id?:  string;
  detail?:    Record<string, unknown>;
}

// ── 상수 ─────────────────────────────────────────────────────────────────────
const MAX_EVENTS  = 500;
const ACTION_COLORS: Record<string, string> = {
  "tenant.create": "#22c55e",
  "tenant.update": "#3b82f6",
  "tenant.delete": "#ef4444",
  "alert.silence_create": "#f59e0b",
  "alert.silence_delete": "#ef4444",
  "kill_switch.toggle": "#ec4899",
};

function actionColor(action: string): string {
  return ACTION_COLORS[action] ?? "#94a3b8";
}

let _seq = 0;
function nextId(): string { return `evt-${++_seq}`; }

// ── TraceModal ────────────────────────────────────────────────────────────────
function TraceModal({
  event, onClose,
}: {
  event: AuditEvent;
  onClose: () => void;
}) {
  return (
    <div
      style={{
        position: "fixed", inset: 0, zIndex: 1000,
        background: "rgba(0,0,0,0.6)", display: "flex",
        alignItems: "center", justifyContent: "center",
      }}
      onClick={onClose}
    >
      <div
        style={{
          background: "var(--bg-2)", border: "1px solid var(--border)",
          borderRadius: 10, padding: 24, minWidth: 420, maxWidth: 560,
          boxShadow: "0 20px 60px rgba(0,0,0,0.5)",
        }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 14 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>감사 이벤트 상세</div>
          <button
            className="btn btn-ghost btn-sm"
            onClick={onClose}
            style={{ fontSize: 16, padding: "0 6px" }}
          >
            ✕
          </button>
        </div>

        <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 12 }}>
          <tbody>
            {[
              ["시각",       event.ts],
              ["액션",       event.action],
              ["Actor",     `${event.actor_id}${event.actor_email ? ` (${event.actor_email})` : ""}`],
              ["환경",       event.env],
              ["리소스",     event.resource_type ? `${event.resource_type} / ${event.resource_id ?? "—"}` : "—"],
              ["Trace ID",  event.trace_id ?? "—"],
            ].map(([k, v]) => (
              <tr key={k} style={{ borderBottom: "1px solid var(--border)" }}>
                <td style={{ padding: "6px 0", color: "var(--text-3)", width: "30%" }}>{k}</td>
                <td style={{ padding: "6px 0", fontFamily: "monospace", wordBreak: "break-all" }}>{v}</td>
              </tr>
            ))}
          </tbody>
        </table>

        {event.detail && Object.keys(event.detail).length > 0 && (
          <div style={{ marginTop: 12 }}>
            <div className="card-title" style={{ marginBottom: 6 }}>Detail</div>
            <pre style={{
              background: "var(--bg-3)", borderRadius: 6, padding: "10px 12px",
              fontSize: 11, overflowX: "auto", color: "var(--text-2)",
              maxHeight: 200,
            }}>
              {JSON.stringify(event.detail, null, 2)}
            </pre>
          </div>
        )}

        {event.trace_id && (
          <div style={{ marginTop: 14, fontSize: 12, color: "#3b82f6" }}>
            Trace ID 로 Sessions 탭에서 세션 검색하여 드릴다운 가능
          </div>
        )}
      </div>
    </div>
  );
}

// ── AuditPage ─────────────────────────────────────────────────────────────────
export default function AuditPage() {
  const sseMsg = useSSE("AUDIT_TAIL");

  const [events,    setEvents]    = useState<AuditEvent[]>([]);
  const [paused,    setPaused]    = useState(false);
  const [actionF,   setActionF]   = useState("");
  const [actorF,    setActorF]    = useState("");
  const [envF,      setEnvF]      = useState("");
  const [selected,  setSelected]  = useState<AuditEvent | null>(null);
  const [sseActive, setSseActive] = useState(false);

  const pendingRef = useRef<AuditEvent[]>([]);
  const bottomRef  = useRef<HTMLDivElement | null>(null);
  const pausedRef  = useRef(false);

  // paused 변경 시 pending flush
  useEffect(() => {
    pausedRef.current = paused;
    if (!paused && pendingRef.current.length > 0) {
      setEvents(prev => {
        const merged = [...prev, ...pendingRef.current];
        pendingRef.current = [];
        return merged.slice(-MAX_EVENTS);
      });
    }
  }, [paused]);

  // SSE 이벤트 수신
  useEffect(() => {
    if (!sseMsg) return;
    setSseActive(true);
    try {
      const raw = JSON.parse(sseMsg.data) as Partial<AuditEvent>;
      if (!raw.action) return; // heartbeat 등 무시
      const evt: AuditEvent = {
        id:           nextId(),
        ts:           raw.ts ?? new Date().toISOString(),
        action:       raw.action,
        actor_id:     raw.actor_id ?? "unknown",
        actor_email:  raw.actor_email,
        env:          raw.env ?? "—",
        resource_type: raw.resource_type,
        resource_id:  raw.resource_id,
        trace_id:     raw.trace_id,
        detail:       raw.detail,
      };

      if (pausedRef.current) {
        pendingRef.current = [...pendingRef.current, evt].slice(-MAX_EVENTS);
      } else {
        setEvents(prev => [...prev, evt].slice(-MAX_EVENTS));
      }
    } catch { /* ignore */ }
  }, [sseMsg]);

  // 자동 스크롤 (paused 아닐 때)
  useEffect(() => {
    if (!paused) {
      bottomRef.current?.scrollIntoView({ behavior: "smooth" });
    }
  }, [events, paused]);

  // 필터 적용
  const filtered = useMemo(() => events.filter(e => {
    if (actionF && !e.action.includes(actionF)) return false;
    if (actorF  && !e.actor_id.includes(actorF) && !(e.actor_email?.includes(actorF))) return false;
    if (envF    && e.env !== envF) return false;
    return true;
  }), [events, actionF, actorF, envF]);

  const clear = useCallback(() => {
    setEvents([]);
    pendingRef.current = [];
  }, []);

  // 고유 env 목록
  const envOptions = useMemo(() =>
    [...new Set(events.map(e => e.env).filter(Boolean))],
  [events]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "calc(100vh - 64px)", gap: 0 }}>
      {/* 헤더 */}
      <div className="page-header">
        <div>
          <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            감사 로그
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              fontSize: 10, color: sseActive ? "#22c55e" : "#94a3b8",
            }}>
              <span style={{
                width: 6, height: 6, borderRadius: "50%",
                background: sseActive ? "#22c55e" : "#94a3b8",
                boxShadow: sseActive ? "0 0 5px #22c55e" : "none",
              }} />
              {sseActive ? "LIVE" : "대기 중"}
            </span>
          </div>
          <div className="page-sub">
            {filtered.length}건 표시 / {events.length}건 수신
            {paused && pendingRef.current.length > 0 && (
              <span style={{ marginLeft: 8, color: "#f59e0b" }}>
                (일시정지 중 — {pendingRef.current.length}건 대기)
              </span>
            )}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {/* 필터 */}
          <input
            placeholder="action 필터"
            value={actionF}
            onChange={e => setActionF(e.target.value)}
            style={{ fontSize: 12, padding: "4px 8px", width: 130, background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)" }}
          />
          <input
            placeholder="actor 필터"
            value={actorF}
            onChange={e => setActorF(e.target.value)}
            style={{ fontSize: 12, padding: "4px 8px", width: 110, background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)" }}
          />
          <select
            value={envF}
            onChange={e => setEnvF(e.target.value)}
            style={{ fontSize: 12, padding: "4px 8px", background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)" }}
          >
            <option value="">전체 환경</option>
            {envOptions.map(env => <option key={env} value={env}>{env}</option>)}
          </select>

          {/* 제어 버튼 */}
          <button
            className={`btn btn-sm ${paused ? "btn-primary" : "btn-ghost"}`}
            onClick={() => setPaused(p => !p)}
            title={paused ? "재개" : "일시정지"}
          >
            {paused ? "▶ 재개" : "⏸ 일시정지"}
          </button>
          <button className="btn btn-ghost btn-sm" onClick={clear} title="로그 초기화">
            🗑
          </button>
        </div>
      </div>

      {/* 로그 테이블 */}
      <div style={{ flex: 1, overflow: "auto", background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: "var(--radius)", margin: "0 0 12px 0" }}>
        {filtered.length === 0 ? (
          <div style={{ padding: 32, textAlign: "center", color: "var(--text-3)", fontSize: 13 }}>
            {events.length === 0
              ? "감사 이벤트 대기 중... (portal:operator 이상 권한 필요)"
              : "필터 조건에 맞는 이벤트가 없습니다."}
          </div>
        ) : (
          <table className="data-table" style={{ fontSize: 12 }}>
            <thead>
              <tr>
                <th style={{ width: 80 }}>시각</th>
                <th style={{ width: 200 }}>액션</th>
                <th>Actor</th>
                <th style={{ width: 80 }}>환경</th>
                <th>리소스</th>
                <th style={{ width: 90 }}>Trace ID</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(e => (
                <tr
                  key={e.id}
                  onClick={() => setSelected(e)}
                  style={{ cursor: "pointer" }}
                >
                  <td style={{ fontSize: 10, color: "var(--text-3)", fontFamily: "monospace" }}>
                    {e.ts.slice(11, 19)}
                  </td>
                  <td>
                    <span style={{
                      display: "inline-block",
                      padding: "1px 7px", borderRadius: 4,
                      fontSize: 11, fontFamily: "monospace",
                      background: `${actionColor(e.action)}22`,
                      color: actionColor(e.action),
                      border: `1px solid ${actionColor(e.action)}44`,
                    }}>
                      {e.action}
                    </span>
                  </td>
                  <td style={{ fontSize: 11, color: "var(--text-2)" }}>
                    {e.actor_email ?? e.actor_id}
                  </td>
                  <td style={{ fontSize: 11 }}>
                    <span style={{
                      padding: "1px 5px", borderRadius: 3,
                      background: e.env === "production" ? "#ef444422" : e.env === "staging" ? "#f59e0b22" : "#3b82f622",
                      color: e.env === "production" ? "#ef4444" : e.env === "staging" ? "#f59e0b" : "#3b82f6",
                      fontSize: 10,
                    }}>
                      {e.env}
                    </span>
                  </td>
                  <td style={{ fontSize: 11, color: "var(--text-3)", fontFamily: "monospace" }}>
                    {e.resource_type ? `${e.resource_type}` : ""}
                    {e.resource_id ? ` / ${e.resource_id.slice(0, 10)}` : ""}
                  </td>
                  <td>
                    {e.trace_id ? (
                      <span
                        style={{ color: "#3b82f6", fontFamily: "monospace", fontSize: 10, cursor: "pointer" }}
                        onClick={ev => { ev.stopPropagation(); setSelected(e); }}
                        title={e.trace_id}
                      >
                        {e.trace_id.slice(0, 8)}…
                      </span>
                    ) : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 상세 모달 */}
      {selected && <TraceModal event={selected} onClose={() => setSelected(null)} />}
    </div>
  );
}
