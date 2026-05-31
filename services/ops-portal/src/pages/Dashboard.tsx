/**
 * 실시간 모니터링 대시보드 (Phase N — N2.3)
 *
 * 변경:
 *   - 5초 polling → useSSE("METRICS") SSE push 방식으로 전환.
 *   - 롤링 히스토리: 최근 60포인트 (appendHistory).
 *   - Stale 감지: STALE_AFTER_MS=10000, SseStatusBadge (LIVE/STALE).
 *   - ReferenceLine: P95_SLO_MS=1200, 에러율 임계값=2%.
 *   - KpiCard stale prop: opacity 0.6 으로 시각적 강조.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  ResponsiveContainer,
  AreaChart, Area,
  LineChart, Line,
  XAxis, YAxis, Tooltip, CartesianGrid, ReferenceLine,
} from "recharts";
import { useSSE } from "../providers/SSEProvider";
import { SSE_CHANNELS } from "../lib/sse";
import type { MetricsSnapshot } from "../lib/api";

// ── 상수 ─────────────────────────────────────────────────────────────────────
const STALE_AFTER_MS = 10_000;          // 마지막 tick 이후 10초 → stale
const MAX_HISTORY    = 60;              // 차트 최대 포인트 수
const P95_SLO_MS    = 1_200;            // SLO 경계선 (plan §slo.md)
const ERR_THRESHOLD = 2;               // 에러율 임계값 (%)
const STT_TARGET    = 400;
const LLM_TARGET    = 800;
const TTS_TARGET    = 200;

// ── 히스토리 타입 ─────────────────────────────────────────────────────────────
interface HistoryPoint { ts: string; value: number }
interface ChartHistory {
  ccu:        HistoryPoint[];
  p95:        HistoryPoint[];
  error_rate: HistoryPoint[];
}

// ── 헬퍼 ─────────────────────────────────────────────────────────────────────
const fmt = (iso: string) => (iso ?? "").slice(11, 16);

function appendHistory(
  prev: ChartHistory,
  snap: MetricsSnapshot,
): ChartHistory {
  const ts = snap.ts;
  const push = <T,>(arr: T[], item: T): T[] =>
    arr.length >= MAX_HISTORY ? [...arr.slice(1), item] : [...arr, item];
  return {
    ccu:        push(prev.ccu,        { ts, value: snap.ccu }),
    p95:        push(prev.p95,        { ts, value: snap.p95_ms }),
    error_rate: push(prev.error_rate, { ts, value: snap.error_rate_pct }),
  };
}

// ── SseStatusBadge ────────────────────────────────────────────────────────────
function SseStatusBadge({ stale }: { stale: boolean }) {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
      <span style={{
        display: "inline-block",
        width: 8, height: 8,
        borderRadius: "50%",
        background: stale ? "#f59e0b" : "#22c55e",
        boxShadow: stale ? "none" : "0 0 6px #22c55e",
        transition: "background 0.3s, box-shadow 0.3s",
      }} />
      <span style={{ fontSize: 11, color: stale ? "#f59e0b" : "#22c55e", letterSpacing: "0.04em" }}>
        {stale ? "STALE" : "LIVE"}
      </span>
    </div>
  );
}

// ── KpiCard ───────────────────────────────────────────────────────────────────
function KpiCard({
  label, value, unit, color, trend, trendDir, stale,
}: {
  label: string;
  value: string | number;
  unit?: string;
  color: string;
  trend?: string;
  trendDir?: "up" | "down" | "neutral";
  stale?: boolean;
}) {
  return (
    <div
      className="kpi-card"
      style={{
        "--kpi-color": color,
        opacity: stale ? 0.6 : 1,
        transition: "opacity 0.4s ease",
      } as React.CSSProperties}
    >
      <div className="kpi-label">{label}</div>
      <div className="kpi-value">
        {value}
        {unit && <span className="kpi-unit">{unit}</span>}
      </div>
      {trend && (
        <div className={`kpi-trend ${trendDir ?? "neutral"}`}>{trend}</div>
      )}
    </div>
  );
}

const TOOLTIP_STYLE = {
  background: "#1a2236", border: "1px solid #273450",
  borderRadius: 6, fontSize: 12, color: "#e2e8f0",
};

// ── Dashboard ─────────────────────────────────────────────────────────────────
export default function Dashboard() {
  const sseMsg = useSSE("METRICS");

  const [metrics, setMetrics] = useState<MetricsSnapshot | null>(null);
  const [history, setHistory] = useState<ChartHistory>({ ccu: [], p95: [], error_rate: [] });
  const [stale,   setStale]   = useState(false);
  const [lastTs,  setLastTs]  = useState("");

  // stale 타이머 ref
  const staleTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const resetStaleTimer = useCallback(() => {
    if (staleTimer.current) clearTimeout(staleTimer.current);
    setStale(false);
    staleTimer.current = setTimeout(() => setStale(true), STALE_AFTER_MS);
  }, []);

  // SSE 메시지 수신 → 상태 갱신
  useEffect(() => {
    if (!sseMsg) return;
    try {
      const snap = JSON.parse(sseMsg.data) as MetricsSnapshot;
      if (snap.error) return; // 서버측 수집 오류면 무시

      setMetrics(snap);
      setHistory(prev => appendHistory(prev, snap));
      setLastTs(snap.ts.slice(11, 19) + " UTC");
      resetStaleTimer();
    } catch {
      /* JSON parse 실패 — heartbeat 등은 무시 */
    }
  }, [sseMsg, resetStaleTimer]);

  // unmount 시 타이머 정리
  useEffect(() => () => {
    if (staleTimer.current) clearTimeout(staleTimer.current);
  }, []);

  // ── 초기 로딩 상태 ─────────────────────────────────────────────────────────
  if (!metrics) {
    return (
      <div>
        <div className="page-header">
          <div>
            <div className="page-title">실시간 모니터링</div>
            <div className="page-sub">SSE 스트림 대기 중...</div>
          </div>
          <SseStatusBadge stale={true} />
        </div>
        <div className="spinner" />
      </div>
    );
  }

  // ── 메인 렌더 ─────────────────────────────────────────────────────────────
  return (
    <div>
      {/* 헤더 */}
      <div className="page-header">
        <div>
          <div className="page-title">실시간 모니터링</div>
          <div className="page-sub">최종 수신: {lastTs}</div>
        </div>
        <SseStatusBadge stale={stale} />
      </div>

      {/* KPI 카드 그리드 */}
      <div className="kpi-grid">
        <KpiCard
          label="현재 동시 통화 (CCU)"
          value={metrics.ccu}
          color="#3b82f6"
          unit="콜"
          trend={`활성 테넌트 ${metrics.active_tenants}`}
          stale={stale}
        />
        <KpiCard
          label="P95 응답 지연"
          value={metrics.p95_ms}
          color="#a855f7"
          unit="ms"
          trend={`P99 ${metrics.p99_ms}ms`}
          trendDir={metrics.p95_ms > P95_SLO_MS ? "down" : "up"}
          stale={stale}
        />
        <KpiCard
          label="에러율"
          value={metrics.error_rate_pct}
          color={metrics.error_rate_pct > ERR_THRESHOLD ? "#ef4444" : "#22c55e"}
          unit="%"
          trend={metrics.error_rate_pct > ERR_THRESHOLD ? "⚠ 임계값 초과" : "정상 범위"}
          trendDir={metrics.error_rate_pct > ERR_THRESHOLD ? "down" : "up"}
          stale={stale}
        />
        <KpiCard
          label="SLO 달성률"
          value={metrics.slo_achieved_pct}
          color={metrics.slo_achieved_pct >= 99 ? "#22c55e" : "#f59e0b"}
          unit="%"
          trend={metrics.slo_achieved_pct >= 99 ? "✓ 목표 달성" : "⚠ 목표 미달"}
          trendDir={metrics.slo_achieved_pct >= 99 ? "up" : "down"}
          stale={stale}
        />
        <KpiCard
          label="오늘 총 통화"
          value={metrics.total_calls_today}
          color="#06b6d4"
          trend={`실패 ${metrics.failed_calls_today}건`}
          trendDir="neutral"
          stale={stale}
        />
        <KpiCard label="STT P95" value={metrics.stt_p95_ms} color="#8b5cf6" unit="ms" stale={stale} />
        <KpiCard label="LLM P95" value={metrics.llm_p95_ms} color="#ec4899" unit="ms" stale={stale} />
        <KpiCard label="TTS P95" value={metrics.tts_p95_ms} color="#14b8a6" unit="ms" stale={stale} />
      </div>

      {/* 차트 그리드 */}
      <div className="chart-grid">
        {/* CCU 추이 */}
        <div className="chart-card">
          <div className="chart-title">동시 통화 수 (CCU)</div>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={history.ccu}>
              <defs>
                <linearGradient id="g-ccu" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#3b82f6" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}   />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#273450" />
              <XAxis dataKey="ts" tickFormatter={fmt} stroke="#475569" tick={{ fontSize: 10 }} />
              <YAxis stroke="#475569" tick={{ fontSize: 10 }} width={32} />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelFormatter={fmt}
                formatter={(v: number) => [`${v} 콜`, "CCU"]}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#3b82f6"
                fill="url(#g-ccu)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* P95 추이 + SLO 경계선 */}
        <div className="chart-card">
          <div className="chart-title">P95 응답 지연 (ms)</div>
          <ResponsiveContainer width="100%" height={180}>
            <LineChart data={history.p95}>
              <CartesianGrid strokeDasharray="3 3" stroke="#273450" />
              <XAxis dataKey="ts" tickFormatter={fmt} stroke="#475569" tick={{ fontSize: 10 }} />
              <YAxis stroke="#475569" tick={{ fontSize: 10 }} width={42} />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelFormatter={fmt}
                formatter={(v: number) => [`${v}ms`, "P95"]}
              />
              <ReferenceLine
                y={P95_SLO_MS}
                stroke="#f59e0b"
                strokeDasharray="4 4"
                label={{ value: `SLO ${P95_SLO_MS}ms`, fill: "#f59e0b", fontSize: 10, position: "insideTopRight" }}
              />
              <Line
                type="monotone"
                dataKey="value"
                stroke="#a855f7"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>

        {/* 에러율 추이 + 임계값 경계선 */}
        <div className="chart-card">
          <div className="chart-title">에러율 (%)</div>
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={history.error_rate}>
              <defs>
                <linearGradient id="g-err" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%"  stopColor="#ef4444" stopOpacity={0.3} />
                  <stop offset="95%" stopColor="#ef4444" stopOpacity={0}   />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#273450" />
              <XAxis dataKey="ts" tickFormatter={fmt} stroke="#475569" tick={{ fontSize: 10 }} />
              <YAxis stroke="#475569" tick={{ fontSize: 10 }} width={32} />
              <Tooltip
                contentStyle={TOOLTIP_STYLE}
                labelFormatter={fmt}
                formatter={(v: number) => [`${v}%`, "에러율"]}
              />
              <ReferenceLine
                y={ERR_THRESHOLD}
                stroke="#ef4444"
                strokeDasharray="4 4"
                label={{ value: `임계 ${ERR_THRESHOLD}%`, fill: "#ef4444", fontSize: 10, position: "insideTopRight" }}
              />
              <Area
                type="monotone"
                dataKey="value"
                stroke="#ef4444"
                fill="url(#g-err)"
                strokeWidth={2}
                dot={false}
                isAnimationActive={false}
              />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      </div>

      {/* 파이프라인 컴포넌트 지연 분해 */}
      <div className="card">
        <div className="card-title">파이프라인 컴포넌트 지연 분해 (P95)</div>
        <div style={{ display: "flex", gap: 12, marginTop: 8, flexWrap: "wrap" }}>
          {[
            { label: "STT", val: metrics.stt_p95_ms, color: "#8b5cf6", target: STT_TARGET },
            { label: "LLM", val: metrics.llm_p95_ms, color: "#ec4899", target: LLM_TARGET },
            { label: "TTS", val: metrics.tts_p95_ms, color: "#14b8a6", target: TTS_TARGET },
          ].map(({ label, val, color, target }) => (
            <div key={label} style={{ flex: 1, minWidth: 140, opacity: stale ? 0.6 : 1, transition: "opacity 0.4s" }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 6, fontSize: 12 }}>
                <span style={{ color: "#94a3b8" }}>{label}</span>
                <span style={{ color: val > target ? "#ef4444" : color, fontWeight: 700 }}>{val}ms</span>
              </div>
              <div style={{ background: "#1a2236", borderRadius: 4, height: 8, overflow: "hidden" }}>
                <div style={{
                  height: "100%", borderRadius: 4,
                  width: `${Math.min(100, (val / (target * 1.5)) * 100)}%`,
                  background: val > target ? "#ef4444" : color,
                  transition: "width 0.4s ease",
                }} />
              </div>
              <div style={{ fontSize: 10, color: "#64748b", marginTop: 3 }}>목표 &lt;{target}ms</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
