/**
 * 시나리오 REST 클라이언트 (/api/v1/scenarios/*).
 *
 * 인증 원칙 (backend/app/core/auth.py):
 *   - 실사용에선 Authorization: Bearer <JWT> 헤더 필수. JWT 안에 tenant_id 포함.
 *   - 개발 환경 (DEV_ALLOW_HEADER_TENANT=true) 은 X-Tenant-Id 헤더로 대체 가능.
 *   - 두 방식 공존 시엔 JWT claim 이 정답. 이 클라이언트는 단순 fetch 만 한다 —
 *     토큰 주입은 호출부에서 apiHeaders() 유틸을 쓴다.
 */
import type { GraphValidationIssue } from "@/lib/dsl";
import type { Scenario } from "@/types/scenario";

export interface ApiCredentials {
  token?: string | null;
  /** dev 환경 전용. prod 에선 절대 사용 금지 — 백엔드가 JWT claim 으로 오버라이드. */
  tenantId?: string | null;
}

function apiHeaders(creds: ApiCredentials): HeadersInit {
  const h: Record<string, string> = { "Content-Type": "application/json" };
  if (creds.token) h["Authorization"] = `Bearer ${creds.token}`;
  if (creds.tenantId) h["X-Tenant-Id"] = creds.tenantId;
  return h;
}

async function okJson<T>(res: Response): Promise<T> {
  if (!res.ok) {
    const text = await res.text();
    throw new Error(`${res.status} ${res.statusText}: ${text}`);
  }
  return (await res.json()) as T;
}

export interface ScenarioListItem {
  scenario_id: string;
  name: string;
  version: number;
  published: boolean;
  updated_at?: string | null;
}

export async function listScenarios(
  creds: ApiCredentials,
): Promise<ScenarioListItem[]> {
  const res = await fetch("/api/v1/scenarios", {
    method: "GET",
    headers: apiHeaders(creds),
  });
  return okJson<ScenarioListItem[]>(res);
}

export async function getScenario(
  scenarioId: string,
  version: number | "latest" | "published" = "latest",
  creds: ApiCredentials = {},
): Promise<Scenario> {
  const res = await fetch(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}?version=${version}`,
    { headers: apiHeaders(creds) },
  );
  return okJson<Scenario>(res);
}

export async function saveScenario(
  scenario: Scenario,
  creds: ApiCredentials = {},
): Promise<Scenario> {
  const res = await fetch("/api/v1/scenarios", {
    method: "POST",
    headers: apiHeaders(creds),
    body: JSON.stringify(scenario),
  });
  return okJson<Scenario>(res);
}

export async function publishScenario(
  scenarioId: string,
  version: number,
  creds: ApiCredentials = {},
): Promise<{ scenario_id: string; version: number; published: boolean }> {
  const res = await fetch(
    `/api/v1/scenarios/${encodeURIComponent(scenarioId)}/publish`,
    {
      method: "POST",
      headers: apiHeaders(creds),
      body: JSON.stringify({ version }),
    },
  );
  return okJson(res);
}

/** 서버측 검증 — DSL 을 저장하기 전에 호출해 상세 오류를 받는다. */
export async function validateScenario(
  scenario: Scenario,
  creds: ApiCredentials = {},
): Promise<{ ok: boolean; issues: GraphValidationIssue[] }> {
  const res = await fetch("/api/v1/scenarios/validate", {
    method: "POST",
    headers: apiHeaders(creds),
    body: JSON.stringify(scenario),
  });
  return okJson(res);
}
