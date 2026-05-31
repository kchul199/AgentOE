/**
 * Kill Switch / 기능 플래그 패널 (Phase N — N4.1)
 *
 * 변경 (N4.1):
 *   - useSSE("ALERTS") 구독 → 알람 이벤트 수신 시 KS 목록 즉시 재조회
 *   - 30s 자동 새로고침 타이머 (SSE 가 조용할 때 baseline)
 *   - SseStatusBadge (LIVE / STALE)
 *   - activated_by 필드 표시
 *   - portal:operator/admin 만 토글 가능 (viewer 는 읽기 전용)
 *   - toggleKillSwitch 시그니처 객체 body 로 통일 (api.ts 맞춤)
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "../providers/AuthProvider";
import { useSSE } from "../providers/SSEProvider";
import { getKillSwitches, toggleKillSwitch, type KillSwitch } from "../lib/api";

// ── 상수 ──────────────────────────────────────────────────────────────────────
const STALE_AFTER_MS        = 10_000;  // 마지막 SSE 이벤트로부터 STALE 판정 기준
const AUTO_REFRESH_INTERVAL = 30_000;  // SSE 조용할 때 폴링 fallback

// ── SseStatusBadge ────────────────────────────────────────────────────────────
function SseStatusBadge({ live }: { live: boolean }) {
  return (
    <span style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      fontSize: 11, fontWeight: 600, letterSpacing: "0.04em",
      padding: "2px 8px", borderRadius: 12,
      background: live ? "#052e16" : "#1c1917",
      color:      live ? "#4ade80" : "#a8a29e",
      border: `1px solid ${live ? "#166534" : "#44403c"}`,
    }}>
      <span style={{
        width: 6, height: 6, borderRadius: "50%",
        background: live ? "#4ade80" : "#78716c",
        boxShadow: live ? "0 0 6px #4ade80" : "none",
        animation: live ? "pulse 2s infinite" : "none",
      }} />
      {live ? "LIVE" : "STALE"}
    </span>
  );
}

// ── 확인 모달 ─────────────────────────────────────────────────────────────────
interface ConfirmState { ks: KillSwitch; targetActive: boolean }

function Modal({ state, onClose, onConfirm }: {
  state: ConfirmState;
  onClose: () => void;
  onConfirm: (reason: string) => void;
}) {
  const [reason, setReason] = useState(state.ks.reason ?? "");
  const activating = state.targetActive;

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title" style={{ color: activating ? "var(--red)" : "var(--green)" }}>
          {activating ? "⚠ 킬스위치 활성화" : "✓ 킬스위치 비활성화"}
        </div>
        <div className="modal-body">
          <div>
            <div className="form-label">대상</div>
            <div style={{ fontSize: 14, fontWeight: 600 }}>{state.ks.label}</div>
            <div style={{ fontSize: 12, color: "var(--text-3)", fontFamily: "monospace" }}>{state.ks.id}</div>
          </div>
          {activating && (
            <div>
              <div className="form-label">활성화 사유 *</div>
              <textarea
                className="input"
                rows={3}
                placeholder="사유를 입력하세요 (필수)"
                value={reason}
                onChange={(e) => setReason(e.target.value)}
                style={{ resize: "vertical" }}
                autoFocus
              />
            </div>
          )}
          {activating && (
            <div style={{
              padding: "10px 12px", background: "#7f1d1d20",
              border: "1px solid var(--red-dim)", borderRadius: 6,
              fontSize: 12, color: "var(--red)",
            }}>
              주의: 이 작업은 즉시 적용됩니다. 해당 서비스/기능이 중단됩니다.
            </div>
          )}
        </div>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>취소</button>
          <button
            className={activating ? "btn btn-danger" : "btn btn-success"}
            disabled={activating && !reason.trim()}
            onClick={() => onConfirm(reason)}
          >
            {activating ? "활성화" : "비활성화"}
          </button>
        </div>
      </div>
    </div>
  );
}

// ── 스코프 상수 ───────────────────────────────────────────────────────────────
const SCOPE_ORDER = ["global", "feature", "tenant"];
const SCOPE_LABEL: Record<string, string> = { global: "전역", feature: "기능", tenant: "테넌트" };

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────
export default function KillSwitchPage() {
  const { roles } = useAuth();
  const sseMsg   = useSSE("ALERTS");   // ALERTS 채널 구독 — KS 이벤트 포함

  const [switches,  setSwitches]  = useState<KillSwitch[]>([]);
  const [confirm,   setConfirm]   = useState<ConfirmState | null>(null);
  const [lastTs,    setLastTs]    = useState<string>("");
  const [sseActive, setSseActive] = useState(false);
  const [loading,   setLoading]   = useState(false);

  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const canOperate = roles.includes("portal:operator") || roles.includes("portal:admin");

  // ── SSE stale 타이머 ────────────────────────────────────────────────────────
  const resetStaleTimer = useCallback(() => {
    setSseActive(true);
    if (staleTimer.current) clearTimeout(staleTimer.current);
    staleTimer.current = setTimeout(() => setSseActive(false), STALE_AFTER_MS);
  }, []);

  useEffect(() => () => { if (staleTimer.current) clearTimeout(staleTimer.current); }, []);

  // ── 데이터 조회 ─────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const ks = await getKillSwitches();
      setSwitches(ks);
      setLastTs(new Date().toLocaleTimeString("ko-KR"));
    } catch { /* 네트워크 오류 — 기존 상태 유지 */ }
    finally { setLoading(false); }
  }, []);

  // 초기 로드
  useEffect(() => { void load(); }, [load]);

  // SSE → 알람 이벤트 수신 시 재조회 (킬스위치 변경 이벤트도 여기로 흐름)
  useEffect(() => {
    if (!sseMsg) return;
    resetStaleTimer();
    void load();
  }, [sseMsg, load, resetStaleTimer]);

  // 30s 자동 새로고침 (SSE 가 조용할 때 fallback)
  useEffect(() => {
    const id = setInterval(() => { void load(); }, AUTO_REFRESH_INTERVAL);
    return () => clearInterval(id);
  }, [load]);

  // ── 토글 핸들러 ──────────────────────────────────────────────────────────────
  const handleToggle = (ks: KillSwitch, next: boolean) => {
    if (!canOperate) return;
    setConfirm({ ks, targetActive: next });
  };

  const handleConfirm = async (reason: string) => {
    if (!confirm) return;
    await toggleKillSwitch(confirm.ks.id, {
      active:   confirm.targetActive,
      reason,
      operator: roles.includes("portal:admin") ? "admin" : "operator",
    });
    setConfirm(null);
    await load();
  };

  // ── 집계 ──────────────────────────────────────────────────────────────────────
  const grouped    = SCOPE_ORDER.map((scope) => ({
    scope,
    items: switches.filter((k) => k.scope === scope),
  }));
  const activeCount = switches.filter((k) => k.active).length;

  // ── 렌더 ─────────────────────────────────────────────────────────────────────
  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">Kill Switch / 기능 플래그</div>
          <div className="page-sub" style={{ display: "flex", alignItems: "center", gap: 10 }}>
            {activeCount > 0
              ? <span style={{ color: "var(--red)" }}>⚠ {activeCount}개 활성화됨</span>
              : <span style={{ color: "var(--green)" }}>✓ 모든 서비스 정상</span>}
            {lastTs && (
              <span style={{ fontSize: 11, color: "var(--text-3)" }}>
                · 마지막 갱신 {lastTs}
              </span>
            )}
            {loading && (
              <span style={{ fontSize: 11, color: "var(--text-3)" }}>갱신 중…</span>
            )}
          </div>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <SseStatusBadge live={sseActive} />
          {!canOperate && (
            <span style={{ fontSize: 11, color: "var(--text-3)", padding: "2px 8px",
              background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 6 }}>
              viewer — 읽기 전용
            </span>
          )}
          <button
            className="btn btn-ghost btn-sm"
            onClick={() => void load()}
            disabled={loading}
          >
            ↻ 새로고침
          </button>
        </div>
      </div>

      {grouped.map(({ scope, items }) => items.length === 0 ? null : (
        <div key={scope} className="ks-section">
          <div className="ks-section-title">
            <span className={`ks-scope-tag ${scope}`}>{SCOPE_LABEL[scope]}</span>
            <span style={{ marginLeft: 8, color: "var(--text-3)", fontWeight: 400 }}>
              ({items.length}개
              {items.filter((i) => i.active).length > 0 &&
                ` · ${items.filter((i) => i.active).length}개 활성`})
            </span>
          </div>

          {items.map((ks) => (
            <div key={ks.id} className={`ks-item ${ks.active ? "active" : ""}`}>
              <span className={`ks-scope-tag ${ks.scope}`}>{SCOPE_LABEL[ks.scope]}</span>

              <div className="ks-label" style={{ flex: 1 }}>
                <div className="ks-name">{ks.label}</div>
                {ks.active && ks.reason && (
                  <div className="ks-reason">⚠ {ks.reason}</div>
                )}
                {ks.active && (ks.activated_by || ks.activated_at) && (
                  <div className="ks-meta">
                    {ks.activated_by && `활성화: ${ks.activated_by}`}
                    {ks.activated_by && ks.activated_at && " · "}
                    {ks.activated_at && ks.activated_at.slice(0, 19).replace("T", " ")}
                  </div>
                )}
                {!ks.active && (
                  <div className="ks-meta" style={{ fontFamily: "monospace", fontSize: 11 }}>
                    {ks.id}
                  </div>
                )}
              </div>

              {/* RBAC-gated 토글 */}
              <label className="toggle" title={canOperate ? "" : "operator 이상 권한 필요"}>
                <input
                  type="checkbox"
                  checked={ks.active}
                  disabled={!canOperate}
                  onChange={(e) => handleToggle(ks, e.target.checked)}
                />
                <span className="toggle-track" />
              </label>
            </div>
          ))}
        </div>
      ))}

      {switches.length === 0 && !loading && (
        <div style={{ textAlign: "center", padding: "48px 0", color: "var(--text-3)", fontSize: 13 }}>
          킬스위치 항목이 없습니다
        </div>
      )}

      {confirm && (
        <Modal
          state={confirm}
          onClose={() => setConfirm(null)}
          onConfirm={(r) => void handleConfirm(r)}
        />
      )}
    </div>
  );
}
