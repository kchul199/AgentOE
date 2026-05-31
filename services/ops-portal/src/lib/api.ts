/**
 * ops-portal API 클라이언트 (Phase N — N2.2).
 *
 * 변경:
 *   - base: /ops-api/api/v1 (mock) → /api/v1 (실 backend).
 *   - csrfHeaders() 자동 주입 (변경 메서드).
 *   - 401 응답 → portalRefresh() → 1회 재시도 → 실패 시 루트 리다이렉트.
 *   - sessions turns API 추가 (N1.8 연동).
 *   - env/info API 추가 (N2.1 연동).
 */

import { csrfHeaders } from "./csrf";
import { portalRefresh } from "./auth";

const BASE = "/api/v1";
const MUTATING = new Set(["POST", "PUT", "PATCH", "DELETE"]);

// ── fetch 코어 ────────────────────────────────────────────────────────────────

async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
  _retry = true,
): Promise<T> {
  const method = (init.method ?? "GET").toUpperCase();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(MUTATING.has(method) ? csrfHeaders() : {}),
    ...(init.headers as Record<string, string> | undefined),
  };

  const res = await fetch(`${BASE}${path}`, {
    credentials: "include",
    ...init,
    headers,
  });

  if (res.status === 401 && _retry) {
    const ok = await portalRefresh();
    if (ok) return apiFetch<T>(path, init, false);
    window.location.href = "/";
    throw new Error("Session expired");
  }

  if (!res.ok) {
    const err = await res.json().catch(() => ({}));
    throw new Error(
      (err as { detail?: string }).detail ?? `${method} ${path} → ${res.status}`
    );
  }

  if (res.status === 204) return undefined as unknown as T;
  return res.json() as Promise<T>;
}

const get  = <T>(path: string)                   => apiFetch<T>(path, { method: "GET" });
const post = <T>(path: string, body: unknown)     => apiFetch<T>(path, { method: "POST",  body: JSON.stringify(body) });
const put  = <T>(path: string, body: unknown)     => apiFetch<T>(path, { method: "PUT",   body: JSON.stringify(body) });
const del  = <T>(path: string)                    => apiFetch<T>(path, { method: "DELETE" });

// ── 환경 정보 (N2.1) ─────────────────────────────────────────────────────────
export const getEnvInfo = () => get<EnvInfo>("/admin/env/info");

// ── 세션 (N1.8 turns 포함) ───────────────────────────────────────────────────
export const getSessions = (params?: SessionListParams) => {
  const q = new URLSearchParams();
  if (params?.tenant_id) q.set("tenant_id", params.tenant_id);
  if (params?.status)    q.set("status",    params.status);
  if (params?.limit != null)  q.set("limit",  String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.size ? "?" + q.toString() : "";
  return get<SessionListResponse>(`/sessions${qs}`);
};

export const getSessionTurns = (sessionId: string, params?: TurnListParams) => {
  const q = new URLSearchParams();
  if (params?.limit  != null) q.set("limit",  String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.size ? "?" + q.toString() : "";
  return get<TurnListResponse>(`/sessions/${sessionId}/turns${qs}`);
};

// ── Kill Switch ───────────────────────────────────────────────────────────────
export const getKillSwitches = () => get<KillSwitch[]>("/kill_switch");
export const toggleKillSwitch = (
  id: string,
  body: { active: boolean; reason: string; operator?: string },
) => post<KillSwitch>(`/kill_switch/${encodeURIComponent(id)}/toggle`, body);

// ── 시나리오 ──────────────────────────────────────────────────────────────────
export const getScenarios = (params?: ScenarioListParams) => {
  const q = new URLSearchParams();
  if (params?.name)      q.set("name",      params.name);
  if (params?.tenant_id) q.set("tenant_id", params.tenant_id);
  if (params?.published != null) q.set("published", String(params.published));
  if (params?.tag)       q.set("tag",       params.tag);
  if (params?.limit  != null) q.set("limit",  String(params.limit));
  if (params?.offset != null) q.set("offset", String(params.offset));
  const qs = q.size ? "?" + q.toString() : "";
  return get<ScenarioListResponse>(`/scenarios${qs}`);
};

export const testScenario = (
  id: string,
  body: { phone_number: string; mock_asr?: string },
) => post<{ test_session_id: string }>(`/scenarios/${encodeURIComponent(id)}/test`, body);

export const deployScenario = (
  id: string,
  body: { env: Env; operator?: string; note?: string },
) => post<{ deployed: boolean; version: number }>(`/scenarios/${encodeURIComponent(id)}/deploy`, body);

// ── Alerts (AM proxy) ─────────────────────────────────────────────────────────
export const getAlerts      = ()                       => get<AlertListResponse>("/admin/alerts");
export const createSilence  = (b: SilenceRequest)      => post<SilenceResponse>("/admin/alerts/silence", b);
export const deleteSilence  = (silenceId: string)      => del<void>(`/admin/alerts/silence/${encodeURIComponent(silenceId)}`);

// ── 환경별 설정 (N5.1) ─────────────────────────────────────────────────────────
export const getConfig    = (env: Env)                            => get<EnvConfig>(`/admin/config/${env}`);
export const getDiff      = ()                                    => get<ConfigDiffResponse>("/admin/config/diff");
export const updateConfig = (env: Env, body: ConfigUpdateBody)   => put<EnvConfig>(`/admin/config/${env}`, body);

// ── 타입 ─────────────────────────────────────────────────────────────────────

/** /stream/metrics SSE metrics.tick 페이로드 (N2.1 get_metrics_snapshot) */
export interface MetricsSnapshot {
  ts: string;
  env: string;
  ccu: number;
  p95_ms: number;
  p99_ms: number;
  error_rate_pct: number;
  slo_achieved_pct: number;
  stt_p95_ms: number;
  llm_p95_ms: number;
  tts_p95_ms: number;
  total_calls_today: number;
  failed_calls_today: number;
  active_tenants: number;
  error?: string;
}

export interface EnvInfo {
  environment: string;
  git_sha: string;
  build_time: string | null;
  server_time: string;
  pod_name: string;
}

export interface SessionListParams {
  tenant_id?: string;
  status?: "active" | "completed" | "failed" | "transferred";
  limit?: number;
  offset?: number;
}

export interface SessionSummary {
  session_id: string;
  tenant_id: string;
  scenario_id: string;
  started_at: string;
  ended_at: string | null;
  duration_s: number | null;
  status: "active" | "completed" | "failed" | "transferred";
  caller_number: string;
  turn_count: number;
  error_count: number;
  trace_id?: string;
}

export interface SessionListResponse {
  total: number;
  items: SessionSummary[];
  next_cursor?: string;
}

export interface TurnListParams {
  limit?: number;
  offset?: number;
}

export interface TurnItem {
  turn: number;
  role: "bot" | "user";
  text: string;
  ts: string;
  latency_ms?: number;
}

export interface TurnListResponse {
  session_id: string;
  total: number;
  items: TurnItem[];
}

export interface KillSwitch {
  id: string;
  scope: string;
  label: string;
  active: boolean;
  activated_at: string | null;
  activated_by: string | null;
  reason: string | null;
}

export type Env = "dev" | "staging" | "prod";

export interface ScenarioSummary {
  scenario_id: string;
  name: string;
  tenant_id: string;
  version: number;
  published: boolean;
  updated_at: string;
  node_count: number;
}

/** 전체 Scenario 객체 — 목록 + 배포 상태 + 태그 */
export interface Scenario extends ScenarioSummary {
  tags: string[];
  env_deployed: Record<Env, string | null>;
}

export interface ScenarioListParams {
  name?: string;
  tenant_id?: string;
  published?: boolean;
  tag?: string;
  limit?: number;
  offset?: number;
}

export interface ScenarioListResponse { total: number; items: Scenario[] }

export interface Alert {
  fingerprint: string;
  status: { state: string; silencedBy: string[]; inhibitedBy: string[] };
  labels: Record<string, string>;
  annotations: Record<string, string>;
  startsAt: string;
  endsAt: string;
}
export interface AlertListResponse { alerts: Alert[] }

export interface SilenceRequest {
  matchers: Array<{ name: string; value: string; isRegex: boolean }>;
  startsAt: string;
  endsAt: string;
  createdBy: string;
  comment: string;
}
export interface SilenceResponse { silenceID: string }

// ── 환경별 설정 타입 (N5.1) ────────────────────────────────────────────────────

/** GET /admin/config/{env} 응답 */
export interface EnvConfig {
  env: Env;
  /** key → string 설정 맵 */
  values: Record<string, string>;
  updated_by: string;
  /** ISO 8601 UTC */
  updated_at: string;
}

/** GET /admin/config/diff 의 키별 차이 항목 */
export interface ConfigDiff {
  key: string;
  dev: string | null;
  staging: string | null;
  prod: string | null;
}

export interface ConfigDiffResponse {
  diffs: ConfigDiff[];
  total: number;
}

/** PUT /admin/config/{env} 요청 body */
export interface ConfigUpdateBody {
  updated_by: string;
  values: Record<string, string>;
}
