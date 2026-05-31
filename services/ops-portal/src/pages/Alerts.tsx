/**
 * 알람 모니터링 페이지 (Phase N — N3.1)
 *
 * - getAlerts() 초기 로드 (portal:viewer+).
 * - useSSE(ALERTS) 실시간 업데이트 — am_poller → Redis → SSE.
 * - severity 필터 (critical / warning / info) + status 필터 (firing / silenced / inhibited).
 * - Silence 생성: alert labels 자동 주입 → matchers 편집 → 기간 선택 → POST /admin/alerts/silence.
 * - Silence 해제: 기존 silenceId 확인 → DELETE /admin/alerts/silence/{id}.
 * - RBAC: getAlerts viewer+, silence 조작 operator+ (backend 에서 강제, UI 는 역할 기반 표시).
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getAlerts, createSilence, deleteSilence,
  type Alert, type SilenceRequest,
} from "../lib/api";
import { useSSE } from "../providers/SSEProvider";
import { SSE_CHANNELS } from "../lib/sse";
import { useAuth } from "../providers/AuthProvider";

// ── 상수 ─────────────────────────────────────────────────────────────────────
const SEVERITY_ORDER = ["critical", "warning", "info", "none"];
const SEVERITY_COLOR: Record<string, string> = {
  critical: "#ef4444",
  warning:  "#f59e0b",
  info:     "#3b82f6",
  none:     "#64748b",
};
const SEVERITY_LABEL: Record<string, string> = {
  critical: "CRITICAL", warning: "WARNING", info: "INFO", none: "NONE",
};

const DURATION_OPTIONS: { label: string; hours: number }[] = [
  { label: "1시간", hours: 1 },
  { label: "4시간", hours: 4 },
  { label: "12시간", hours: 12 },
  { label: "24시간", hours: 24 },
  { label: "7일",   hours: 168 },
];

// ── 헬퍼 ─────────────────────────────────────────────────────────────────────
function severity(alert: Alert): string {
  return (alert.labels["severity"] ?? "none").toLowerCase();
}
function alertState(alert: Alert): "silenced" | "inhibited" | "firing" {
  if (alert.status.silencedBy.length > 0) return "silenced";
  if (alert.status.inhibitedBy.length > 0) return "inhibited";
  return "firing";
}
function isoOffset(hours: number): string {
  return new Date(Date.now() + hours * 3_600_000).toISOString();
}

// ── SeverityBadge ─────────────────────────────────────────────────────────────
function SeverityBadge({ sev }: { sev: string }) {
  const color = SEVERITY_COLOR[sev] ?? "#64748b";
  return (
    <span style={{
      padding: "1px 7px", borderRadius: 4, fontSize: 10, fontWeight: 700,
      letterSpacing: "0.05em", background: `${color}22`,
      color, border: `1px solid ${color}44`,
    }}>
      {SEVERITY_LABEL[sev] ?? sev.toUpperCase()}
    </span>
  );
}

// ── StateBadge ────────────────────────────────────────────────────────────────
function StateBadge({ state }: { state: ReturnType<typeof alertState> }) {
  const map = {
    firing:    { color: "#ef4444", label: "발화 중" },
    silenced:  { color: "#94a3b8", label: "무음 처리" },
    inhibited: { color: "#6366f1", label: "억제됨" },
  } as const;
  const { color, label } = map[state];
  return (
    <span style={{
      padding: "1px 7px", borderRadius: 4, fontSize: 10,
      background: `${color}22`, color, border: `1px solid ${color}44`,
    }}>
      {label}
    </span>
  );
}

// ── SilenceModal ──────────────────────────────────────────────────────────────
interface Matcher { name: string; value: string; isRegex: boolean }

function SilenceModal({
  alert, createdBy, onClose, onCreated,
}: {
  alert: Alert;
  createdBy: string;
  onClose: () => void;
  onCreated: () => void;
}) {
  // matchers 초기값 — alert labels 전체
  const initMatchers: Matcher[] = Object.entries(alert.labels).map(([name, value]) => ({
    name, value, isRegex: false,
  }));
  const [matchers, setMatchers] = useState<Matcher[]>(initMatchers);
  const [durationHours, setDurationHours] = useState(1);
  const [comment, setComment] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState("");

  const updateMatcher = (idx: number, field: keyof Matcher, val: string | boolean) => {
    setMatchers(prev => prev.map((m, i) => i === idx ? { ...m, [field]: val } : m));
  };
  const removeMatcher = (idx: number) => {
    setMatchers(prev => prev.filter((_, i) => i !== idx));
  };
  const addMatcher = () => {
    setMatchers(prev => [...prev, { name: "", value: "", isRegex: false }]);
  };

  const handleSubmit = async () => {
    if (!comment.trim()) { setError("코멘트를 입력하세요."); return; }
    if (matchers.some(m => !m.name)) { setError("Matcher 이름을 모두 입력하세요."); return; }
    setSubmitting(true);
    setError("");
    try {
      const body: SilenceRequest = {
        matchers: matchers.map(m => ({ name: m.name, value: m.value, isRegex: m.isRegex })),
        startsAt: new Date().toISOString(),
        endsAt:   isoOffset(durationHours),
        createdBy,
        comment: comment.trim(),
      };
      await createSilence(body);
      onCreated();
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : "요청 실패");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <div
      style={{ position: "fixed", inset: 0, zIndex: 1000, background: "rgba(0,0,0,0.6)", display: "flex", alignItems: "center", justifyContent: "center" }}
      onClick={onClose}
    >
      <div
        style={{ background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 10, padding: 24, width: 540, maxHeight: "80vh", overflow: "auto", boxShadow: "0 20px 60px rgba(0,0,0,0.5)" }}
        onClick={e => e.stopPropagation()}
      >
        <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 16 }}>
          <div style={{ fontSize: 14, fontWeight: 700 }}>⏸ Silence 생성</div>
          <button className="btn btn-ghost btn-sm" onClick={onClose} style={{ fontSize: 16, padding: "0 6px" }}>✕</button>
        </div>

        {/* Alert 요약 */}
        <div style={{ background: "var(--bg-3)", borderRadius: 6, padding: "10px 12px", marginBottom: 16, fontSize: 12 }}>
          <div style={{ fontWeight: 700, marginBottom: 4 }}>
            {alert.labels["alertname"] ?? alert.fingerprint.slice(0, 8)}
          </div>
          <div style={{ color: "var(--text-3)" }}>{alert.annotations["summary"] ?? alert.annotations["description"] ?? "—"}</div>
        </div>

        {/* Matchers */}
        <div className="form-label" style={{ marginBottom: 6 }}>Matchers</div>
        {matchers.map((m, idx) => (
          <div key={idx} style={{ display: "flex", gap: 6, marginBottom: 6, alignItems: "center" }}>
            <input
              placeholder="label"
              value={m.name}
              onChange={e => updateMatcher(idx, "name", e.target.value)}
              style={{ flex: "0 0 110px", fontSize: 12, padding: "4px 8px", background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)", fontFamily: "monospace" }}
            />
            <span style={{ color: "var(--text-3)", fontSize: 12 }}>{m.isRegex ? "=~" : "="}</span>
            <input
              placeholder="value"
              value={m.value}
              onChange={e => updateMatcher(idx, "value", e.target.value)}
              style={{ flex: 1, fontSize: 12, padding: "4px 8px", background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)", fontFamily: "monospace" }}
            />
            <label style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: "var(--text-3)", whiteSpace: "nowrap" }}>
              <input type="checkbox" checked={m.isRegex} onChange={e => updateMatcher(idx, "isRegex", e.target.checked)} />
              Regex
            </label>
            <button className="btn btn-ghost btn-sm" onClick={() => removeMatcher(idx)} style={{ padding: "2px 6px", color: "#ef4444" }}>✕</button>
          </div>
        ))}
        <button className="btn btn-ghost btn-sm" onClick={addMatcher} style={{ fontSize: 11, marginBottom: 16 }}>+ Matcher 추가</button>

        {/* 기간 */}
        <div className="form-label" style={{ marginBottom: 6 }}>무음 기간</div>
        <div style={{ display: "flex", gap: 8, marginBottom: 16, flexWrap: "wrap" }}>
          {DURATION_OPTIONS.map(opt => (
            <button
              key={opt.hours}
              className={`btn btn-sm ${durationHours === opt.hours ? "btn-primary" : "btn-ghost"}`}
              onClick={() => setDurationHours(opt.hours)}
            >
              {opt.label}
            </button>
          ))}
        </div>
        <div style={{ fontSize: 11, color: "var(--text-3)", marginBottom: 16 }}>
          종료: {new Date(Date.now() + durationHours * 3_600_000).toLocaleString("ko-KR")}
        </div>

        {/* 코멘트 */}
        <div className="form-label" style={{ marginBottom: 6 }}>코멘트 *</div>
        <textarea
          rows={2}
          placeholder="무음 처리 사유를 입력하세요"
          value={comment}
          onChange={e => setComment(e.target.value)}
          style={{ width: "100%", boxSizing: "border-box", fontSize: 12, padding: "8px 10px", background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)", resize: "vertical" }}
        />

        {error && <div style={{ marginTop: 8, fontSize: 12, color: "#ef4444" }}>{error}</div>}

        <div style={{ display: "flex", gap: 8, marginTop: 16, justifyContent: "flex-end" }}>
          <button className="btn btn-ghost" onClick={onClose}>취소</button>
          <button className="btn btn-primary" disabled={submitting} onClick={() => void handleSubmit()}>
            {submitting ? "처리 중..." : "Silence 생성"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── AlertCard ─────────────────────────────────────────────────────────────────
function AlertCard({
  alert, canSilence, onSilence, onUnsilence,
}: {
  alert: Alert;
  canSilence: boolean;
  onSilence: (a: Alert) => void;
  onUnsilence: (silenceId: string) => void;
}) {
  const sev   = severity(alert);
  const state = alertState(alert);
  const name  = alert.labels["alertname"] ?? alert.fingerprint.slice(0, 8);
  const summary = alert.annotations["summary"] ?? alert.annotations["description"] ?? "";
  const color = SEVERITY_COLOR[sev] ?? "#64748b";

  return (
    <div style={{
      borderLeft: `3px solid ${color}`,
      background: "var(--bg-2)",
      border: "1px solid var(--border)",
      borderLeftColor: color,
      borderRadius: "0 6px 6px 0",
      padding: "12px 14px",
      marginBottom: 8,
      display: "flex", gap: 12, alignItems: "flex-start",
    }}>
      <div style={{ flex: 1 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", marginBottom: 4, flexWrap: "wrap" }}>
          <SeverityBadge sev={sev} />
          <StateBadge state={state} />
          <span style={{ fontSize: 13, fontWeight: 700, marginLeft: 2 }}>{name}</span>
        </div>
        {summary && (
          <div style={{ fontSize: 12, color: "var(--text-2)", marginBottom: 6 }}>{summary}</div>
        )}
        {/* Labels */}
        <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
          {Object.entries(alert.labels)
            .filter(([k]) => !["alertname", "severity"].includes(k))
            .map(([k, v]) => (
              <span key={k} style={{ fontSize: 10, fontFamily: "monospace", padding: "1px 5px", borderRadius: 3, background: "var(--bg-3)", color: "var(--text-3)" }}>
                {k}={v}
              </span>
            ))}
        </div>
        <div style={{ fontSize: 10, color: "var(--text-3)", marginTop: 6 }}>
          시작: {alert.startsAt.slice(0, 19).replace("T", " ")}
          {state === "silenced" && (
            <span style={{ marginLeft: 12, color: "#94a3b8" }}>
              Silence: {alert.status.silencedBy.join(", ")}
            </span>
          )}
        </div>
      </div>

      {/* 액션 버튼 */}
      {canSilence && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6, flexShrink: 0 }}>
          {state === "firing" && (
            <button className="btn btn-ghost btn-sm" onClick={() => onSilence(alert)} style={{ fontSize: 11, whiteSpace: "nowrap" }}>
              ⏸ 무음 처리
            </button>
          )}
          {state === "silenced" && alert.status.silencedBy.map(sid => (
            <button
              key={sid}
              className="btn btn-ghost btn-sm"
              onClick={() => onUnsilence(sid)}
              style={{ fontSize: 11, color: "#f59e0b", whiteSpace: "nowrap" }}
            >
              ▶ 무음 해제
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ── Alerts (메인) ─────────────────────────────────────────────────────────────
export default function Alerts() {
  const { roles } = useAuth();
  const canSilence = roles.includes("portal:operator") || roles.includes("portal:admin");
  const userEmail  = "operator"; // auth 에서 이메일 추출 가능하면 대체

  const sseMsg = useSSE("ALERTS");

  const [alerts,    setAlerts]    = useState<Alert[]>([]);
  const [loading,   setLoading]   = useState(true);
  const [sseActive, setSseActive] = useState(false);
  const [sevFilter, setSevFilter] = useState<string>("");
  const [stateFilter, setStateFilter] = useState<string>("");
  const [silenceTarget, setSilenceTarget] = useState<Alert | null>(null);
  const [unsilencing,  setUnsilencing]   = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await getAlerts();
      setAlerts(r.alerts ?? []);
    } catch { /* ignore */ } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  // SSE 실시간 업데이트
  useEffect(() => {
    if (!sseMsg) return;
    setSseActive(true);
    try {
      const payload = JSON.parse(sseMsg.data);
      // am_poller 가 publish 하는 포맷: { alerts: Alert[] } 또는 단일 Alert
      if (Array.isArray(payload?.alerts)) {
        setAlerts(payload.alerts as Alert[]);
      } else if (payload?.fingerprint) {
        // 단일 alert 업데이트 — upsert
        setAlerts(prev => {
          const idx = prev.findIndex(a => a.fingerprint === (payload as Alert).fingerprint);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = payload as Alert;
            return next;
          }
          return [payload as Alert, ...prev];
        });
      }
    } catch { /* heartbeat 등 무시 */ }
  }, [sseMsg]);

  const handleUnsilence = async (silenceId: string) => {
    setUnsilencing(silenceId);
    try {
      await deleteSilence(silenceId);
      await load(); // 목록 갱신
    } catch { /* ignore */ } finally {
      setUnsilencing(null);
    }
  };

  // 필터 적용
  const filtered = useMemo(() => alerts.filter(a => {
    if (sevFilter   && severity(a) !== sevFilter)   return false;
    if (stateFilter && alertState(a) !== stateFilter) return false;
    return true;
  }), [alerts, sevFilter, stateFilter]);

  // severity 별 그룹
  const grouped = useMemo(() =>
    SEVERITY_ORDER.map(sev => ({
      sev,
      items: filtered.filter(a => severity(a) === sev),
    })).filter(g => g.items.length > 0),
  [filtered]);

  const firingCount  = alerts.filter(a => alertState(a) === "firing").length;
  const critCount    = alerts.filter(a => severity(a) === "critical" && alertState(a) === "firing").length;

  return (
    <div>
      {/* 헤더 */}
      <div className="page-header">
        <div>
          <div className="page-title" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            알람 모니터링
            <span style={{
              display: "inline-flex", alignItems: "center", gap: 4,
              fontSize: 10, color: sseActive ? "#22c55e" : "#94a3b8",
            }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: sseActive ? "#22c55e" : "#94a3b8", boxShadow: sseActive ? "0 0 5px #22c55e" : "none" }} />
              {sseActive ? "LIVE" : "연결 중"}
            </span>
          </div>
          <div className="page-sub">
            {critCount > 0
              ? <span style={{ color: "#ef4444" }}>⚠ CRITICAL {critCount}건 발화 중</span>
              : firingCount > 0
                ? <span style={{ color: "#f59e0b" }}>발화 {firingCount}건 / 전체 {alerts.length}건</span>
                : <span style={{ color: "#22c55e" }}>✓ 발화 알람 없음 (전체 {alerts.length}건)</span>}
          </div>
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {/* 필터 */}
          <select
            value={sevFilter}
            onChange={e => setSevFilter(e.target.value)}
            style={{ fontSize: 12, padding: "4px 8px", background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)" }}
          >
            <option value="">전체 심각도</option>
            <option value="critical">Critical</option>
            <option value="warning">Warning</option>
            <option value="info">Info</option>
          </select>
          <select
            value={stateFilter}
            onChange={e => setStateFilter(e.target.value)}
            style={{ fontSize: 12, padding: "4px 8px", background: "var(--bg-3)", border: "1px solid var(--border)", borderRadius: 4, color: "var(--text)" }}
          >
            <option value="">전체 상태</option>
            <option value="firing">발화 중</option>
            <option value="silenced">무음 처리</option>
            <option value="inhibited">억제됨</option>
          </select>
          <button className="btn btn-ghost btn-sm" onClick={() => void load()}>↻ 새로고침</button>
        </div>
      </div>

      {/* KPI 배지 행 */}
      <div style={{ display: "flex", gap: 10, marginBottom: 16, flexWrap: "wrap" }}>
        {SEVERITY_ORDER.map(sev => {
          const cnt = alerts.filter(a => severity(a) === sev).length;
          if (!cnt) return null;
          const color = SEVERITY_COLOR[sev];
          return (
            <div
              key={sev}
              onClick={() => setSevFilter(prev => prev === sev ? "" : sev)}
              style={{
                cursor: "pointer",
                padding: "6px 14px", borderRadius: 6,
                background: sevFilter === sev ? `${color}33` : "var(--bg-2)",
                border: `1px solid ${sevFilter === sev ? color : "var(--border)"}`,
                color,
                fontSize: 12, fontWeight: 700,
              }}
            >
              {SEVERITY_LABEL[sev]} {cnt}
            </div>
          );
        })}
      </div>

      {/* 알람 목록 */}
      {loading ? (
        <div className="spinner" />
      ) : filtered.length === 0 ? (
        <div style={{ padding: 32, textAlign: "center", color: "var(--text-3)", fontSize: 13 }}>
          {alerts.length === 0 ? "현재 발생한 알람이 없습니다." : "필터 조건에 맞는 알람이 없습니다."}
        </div>
      ) : (
        grouped.map(({ sev, items }) => (
          <div key={sev} style={{ marginBottom: 20 }}>
            <div style={{ fontSize: 11, fontWeight: 700, color: SEVERITY_COLOR[sev], letterSpacing: "0.08em", marginBottom: 8 }}>
              {SEVERITY_LABEL[sev]} ({items.length})
            </div>
            {items.map(a => (
              <AlertCard
                key={a.fingerprint}
                alert={a}
                canSilence={canSilence}
                onSilence={setSilenceTarget}
                onUnsilence={sid => {
                  if (unsilencing === sid) return;
                  void handleUnsilence(sid);
                }}
              />
            ))}
          </div>
        ))
      )}

      {/* Silence 생성 모달 */}
      {silenceTarget && (
        <SilenceModal
          alert={silenceTarget}
          createdBy={userEmail}
          onClose={() => setSilenceTarget(null)}
          onCreated={() => void load()}
        />
      )}
    </div>
  );
}
