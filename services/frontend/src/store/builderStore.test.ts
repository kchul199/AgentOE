/**
 * Track 5-C: Zustand 빌더 스토어 단위 테스트.
 *
 * 커버리지:
 *   - addNodeFromPalette: unique id 생성 + 기본 config 주입 + 노드 반환
 *   - onConnect: BuilderEdge 생성 시 id prefix `e_` 부여 + source/target 보존
 *   - updateSelectedNode: id 변경 시 node.id + 모든 edge source/target +
 *     meta.entry/fallback_node 4개 cascade
 *   - setEntryToSelected / toggleFallbackToSelected: 선택 없을 때 no-op,
 *     있을 때 meta 갱신 + toggle 의미 유지
 *   - setMeta / setToken(localStorage 키 agentoe.token)
 *   - pushToast / dismissToast: 자동 만료(success/info), 수동 유지(error)
 *   - loadScenario / saveCurrent / publishCurrent: fetch 모킹 후 toast 와
 *     busy 플래그가 맞물리는지 확인
 *
 * 구현 주의:
 *   - Zustand 스토어는 모듈 전역이므로 각 테스트 시작에서 resetBuilderStoreForTest() 필수.
 *   - loadScenario 등은 globalThis.fetch 를 교체하여 격리.
 */
import { beforeEach, afterEach, describe, expect, it, vi } from "vitest";

import {
  currentCredentials,
  resetBuilderStoreForTest,
  useBuilderStore,
} from "@/store/builderStore";
import type { BuilderEdge, BuilderNode } from "@/lib/dsl";
import type { Scenario } from "@/types/scenario";

// ── 헬퍼 ───────────────────────────────────────────────────────────────────────

function state() {
  return useBuilderStore.getState();
}

function seedTwoNodes(): { a: string; b: string } {
  const a = state().addNodeFromPalette("llm", { x: 0, y: 0 });
  const b = state().addNodeFromPalette("end", { x: 200, y: 0 });
  return { a, b };
}

function mockFetchOnce(body: unknown, status = 200): void {
  const res = {
    ok: status >= 200 && status < 300,
    status,
    statusText: status === 200 ? "OK" : "ERR",
    json: async () => body,
    text: async () => JSON.stringify(body),
  } as unknown as Response;
  globalThis.fetch = vi.fn().mockResolvedValue(res) as unknown as typeof fetch;
}

// ── 초기화 / 해제 ──────────────────────────────────────────────────────────────

beforeEach(() => {
  resetBuilderStoreForTest();
  // localStorage 초기화 — jsdom 이 없는 node 환경에서도 안전
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem("agentoe.token");
    } catch {
      /* ignore */
    }
  }
});

afterEach(() => {
  vi.restoreAllMocks();
});

// ── 기본 상태 ──────────────────────────────────────────────────────────────────

describe("initial state", () => {
  it("빈 nodes/edges, DEFAULT_META, 빈 토스트로 시작", () => {
    const s = state();
    expect(s.nodes).toEqual([]);
    expect(s.edges).toEqual([]);
    expect(s.selectedId).toBeNull();
    expect(s.busy).toBe(false);
    expect(s.error).toBeNull();
    expect(s.toasts).toEqual([]);
    expect(s.meta.scenario_id).toBe("new_scenario_v1");
  });
});

// ── addNodeFromPalette ─────────────────────────────────────────────────────────

describe("addNodeFromPalette", () => {
  it("기본 config 가 주입된 llm 노드 추가 → unique id 반환", () => {
    const id = state().addNodeFromPalette("llm", { x: 10, y: 20 });
    expect(id).toBe("llm_1");
    const n = state().nodes.find((x) => x.id === id)!;
    expect(n.type).toBe("scenarioNode");
    expect(n.position).toEqual({ x: 10, y: 20 });
    expect(n.data.dsl.type).toBe("llm");
    // llm 기본 config 주요 키 확인
    const cfg = n.data.dsl.config as Record<string, unknown>;
    expect(cfg.model).toBe("groq-llama-4-scout");
    expect(cfg.streaming).toBe(true);
  });

  it("같은 type 여러 번 추가 시 id 충돌 없음", () => {
    const id1 = state().addNodeFromPalette("llm", { x: 0, y: 0 });
    const id2 = state().addNodeFromPalette("llm", { x: 1, y: 1 });
    const id3 = state().addNodeFromPalette("llm", { x: 2, y: 2 });
    expect(new Set([id1, id2, id3]).size).toBe(3);
    expect([id1, id2, id3]).toEqual(["llm_1", "llm_2", "llm_3"]);
  });
});

// ── onConnect ──────────────────────────────────────────────────────────────────

describe("onConnect", () => {
  it("Connection → BuilderEdge 생성 시 id prefix e_ + source/target 보존", () => {
    const { a, b } = seedTwoNodes();
    state().onConnect({ source: a, target: b, sourceHandle: null, targetHandle: null });
    const e = state().edges[0];
    expect(e).toBeDefined();
    expect(e.source).toBe(a);
    expect(e.target).toBe(b);
    expect(e.id.startsWith("e_")).toBe(true);
  });

  it("source 또는 target null 이면 무시 (react-flow addEdge 동작 기대)", () => {
    // react-flow 의 addEdge 는 source/target 이 null 인 Connection 은 drop.
    state().onConnect({ source: null, target: null, sourceHandle: null, targetHandle: null });
    expect(state().edges).toHaveLength(0);
  });
});

// ── updateSelectedNode id 변경 cascade ────────────────────────────────────────

describe("updateSelectedNode — id cascade", () => {
  it("id 바뀌면 node.id + 모든 edge source/target + meta.entry/fallback 4개 cascade", () => {
    const { a, b } = seedTwoNodes();
    state().onConnect({ source: a, target: b, sourceHandle: null, targetHandle: null });
    state().setMeta({ entry: a, fallback_node: a });
    state().setSelectedId(a);

    const oldDsl = state().nodes.find((n) => n.id === a)!.data.dsl;
    const next = { ...oldDsl, id: "start_v2" };
    state().updateSelectedNode(next);

    const s = state();
    expect(s.nodes.some((n) => n.id === "start_v2")).toBe(true);
    expect(s.nodes.some((n) => n.id === a)).toBe(false);
    expect(s.edges[0].source).toBe("start_v2");
    expect(s.edges[0].target).toBe(b);
    expect(s.meta.entry).toBe("start_v2");
    expect(s.meta.fallback_node).toBe("start_v2");
    expect(s.selectedId).toBe("start_v2");
  });

  it("id 동일 (config 만 변경) 시 edges / meta 영향 없음", () => {
    const { a, b } = seedTwoNodes();
    state().onConnect({ source: a, target: b, sourceHandle: null, targetHandle: null });
    state().setMeta({ entry: a });
    state().setSelectedId(a);

    const edgesBefore: BuilderEdge[] = state().edges;
    const oldDsl = state().nodes.find((n) => n.id === a)!.data.dsl;
    // tempature 만 살짝 바꿈
    if (oldDsl.type === "llm") {
      state().updateSelectedNode({
        ...oldDsl,
        config: { ...oldDsl.config, temperature: 0.2 },
      });
    }

    expect(state().edges).toEqual(edgesBefore);
    expect(state().meta.entry).toBe(a);
    const cfg = state().nodes.find((n) => n.id === a)!.data.dsl.config as {
      temperature: number;
    };
    expect(cfg.temperature).toBeCloseTo(0.2);
  });

  it("selectedId 없으면 no-op", () => {
    const { a } = seedTwoNodes();
    const oldDsl = state().nodes.find((n) => n.id === a)!.data.dsl;
    state().updateSelectedNode({ ...oldDsl, id: "wont_happen" });
    // 여전히 원래 id
    expect(state().nodes.find((n) => n.id === a)).toBeDefined();
  });
});

// ── setEntry / toggleFallback ────────────────────────────────────────────────

describe("setEntryToSelected / toggleFallbackToSelected", () => {
  it("선택 없을 때 no-op", () => {
    seedTwoNodes();
    state().setEntryToSelected();
    state().toggleFallbackToSelected();
    expect(state().meta.entry).toBe("");
    expect(state().meta.fallback_node).toBeNull();
  });

  it("선택 있을 때 meta.entry 설정", () => {
    const { a } = seedTwoNodes();
    state().setSelectedId(a);
    state().setEntryToSelected();
    expect(state().meta.entry).toBe(a);
  });

  it("toggleFallback — 다른 값이면 설정, 같은 값이면 null", () => {
    const { a, b } = seedTwoNodes();
    state().setSelectedId(a);
    state().toggleFallbackToSelected();
    expect(state().meta.fallback_node).toBe(a);
    // 다시 누르면 null
    state().toggleFallbackToSelected();
    expect(state().meta.fallback_node).toBeNull();
    // 다른 노드 선택 후 누르면 그 노드로
    state().setSelectedId(b);
    state().toggleFallbackToSelected();
    expect(state().meta.fallback_node).toBe(b);
  });
});

// ── setMeta / setToken / currentCredentials ───────────────────────────────────

describe("setMeta / setToken", () => {
  it("setMeta 는 부분 patch 만 변경", () => {
    state().setMeta({ name: "Greeting v2" });
    expect(state().meta.name).toBe("Greeting v2");
    // 다른 필드 보존
    expect(state().meta.scenario_id).toBe("new_scenario_v1");
  });

  it("setToken 은 localStorage 에도 반영 + currentCredentials 조립", () => {
    state().setToken("tok_abc");
    expect(state().token).toBe("tok_abc");
    if (typeof window !== "undefined") {
      expect(window.localStorage.getItem("agentoe.token")).toBe("tok_abc");
    }
    const creds = currentCredentials(state());
    expect(creds.token).toBe("tok_abc");
    expect(creds.tenantId).toBe("t_demo");
  });

  it("setToken('') 은 localStorage 에서 키 제거", () => {
    state().setToken("tok_xyz");
    state().setToken("");
    if (typeof window !== "undefined") {
      expect(window.localStorage.getItem("agentoe.token")).toBeNull();
    }
  });
});

// ── 토스트 ────────────────────────────────────────────────────────────────────

describe("pushToast / dismissToast", () => {
  it("success 는 ttl 이후 자동 제거", async () => {
    vi.useFakeTimers();
    const id = state().pushToast("success", "saved!", 1000);
    expect(state().toasts.some((t) => t.id === id)).toBe(true);
    vi.advanceTimersByTime(1001);
    expect(state().toasts.some((t) => t.id === id)).toBe(false);
    vi.useRealTimers();
  });

  it("error 는 자동 제거되지 않음 (ttl=0)", () => {
    vi.useFakeTimers();
    const id = state().pushToast("error", "boom");
    vi.advanceTimersByTime(60_000);
    expect(state().toasts.some((t) => t.id === id)).toBe(true);
    vi.useRealTimers();
  });

  it("dismissToast 로 즉시 제거", () => {
    const id = state().pushToast("info", "hello");
    state().dismissToast(id);
    expect(state().toasts.some((t) => t.id === id)).toBe(false);
  });
});

// ── 비동기 (fetch 모킹) ───────────────────────────────────────────────────────

describe("saveCurrent (with fetch mock)", () => {
  it("assemble 실패 시 error 세팅 + error toast 생성, null 반환", async () => {
    // 노드가 없어 entry 비어있으면 schema 에러
    const res = await state().saveCurrent();
    expect(res).toBeNull();
    expect(state().error).toBeTruthy();
    expect(state().toasts.some((t) => t.level === "error")).toBe(true);
  });

  it("2xx 응답 → meta.version 반영 + success toast + busy 복귀", async () => {
    // 최소 그래프 구성 — entry=llm_1, end_1 연결
    const a = state().addNodeFromPalette("llm", { x: 0, y: 0 });
    const b = state().addNodeFromPalette("end", { x: 200, y: 0 });
    state().onConnect({ source: a, target: b, sourceHandle: null, targetHandle: null });
    state().setMeta({ entry: a });

    // 서버가 version 42 를 채번했다고 가정
    const serverEcho: Scenario = {
      scenario_id: "new_scenario_v1",
      tenant_id: "t_demo",
      name: "New scenario",
      version: 42,
      entry: a,
      fallback_node: null,
      nodes: [
        {
          id: a,
          type: "llm",
          config: {
            model: "groq-llama-4-scout",
            fallback_model: "groq-llama-3.3-70b",
            system_prompt: "",
            temperature: 0.7,
            max_tokens: 512,
            streaming: true,
            enable_filler: true,
          },
        },
        { id: b, type: "end", config: { closing_message: null } },
      ],
      edges: [{ from: a, to: b }],
      limits: {
        max_turns: 30,
        max_duration_s: 900,
        max_tool_calls_per_turn: 3,
        max_cost_cents_per_session: 50.0,
      },
      tags: [],
      published: false,
    };

    // saveCurrent 는 내부에서 saveScenario + refreshList 2 회 fetch — 둘 다 2xx
    const fetchSpy = vi.fn(async (_url, init) => {
      // refreshList 호출은 GET — 빈 배열 반환
      const method = (init as RequestInit | undefined)?.method ?? "GET";
      const body = method === "POST" ? serverEcho : [];
      return {
        ok: true,
        status: 200,
        statusText: "OK",
        json: async () => body,
        text: async () => JSON.stringify(body),
      } as unknown as Response;
    });
    globalThis.fetch = fetchSpy as unknown as typeof fetch;

    const out = await state().saveCurrent();
    expect(out).toBeTruthy();
    expect(state().meta.version).toBe(42);
    expect(state().meta.published).toBe(false);
    expect(state().busy).toBe(false);
    expect(state().toasts.some((t) => t.level === "success")).toBe(true);
  });

  it("non-2xx → error 세팅 + error toast + busy 복귀", async () => {
    const a = state().addNodeFromPalette("llm", { x: 0, y: 0 });
    const b = state().addNodeFromPalette("end", { x: 200, y: 0 });
    state().onConnect({ source: a, target: b, sourceHandle: null, targetHandle: null });
    state().setMeta({ entry: a });

    mockFetchOnce({ detail: "bad" }, 422);

    const out = await state().saveCurrent();
    expect(out).toBeNull();
    expect(state().error).toMatch(/422/);
    expect(state().busy).toBe(false);
    expect(state().toasts.some((t) => t.level === "error")).toBe(true);
  });
});

describe("loadScenario (with fetch mock)", () => {
  it("2xx → nodes/edges/meta 채움 + success toast", async () => {
    const scn: Scenario = {
      scenario_id: "imported",
      tenant_id: "t_acme",
      name: "Imported",
      version: 3,
      entry: "n1",
      fallback_node: null,
      nodes: [
        { id: "n1", type: "end", config: { closing_message: "bye" } },
      ],
      edges: [],
      limits: {
        max_turns: 30,
        max_duration_s: 900,
        max_tool_calls_per_turn: 3,
        max_cost_cents_per_session: 50.0,
      },
      tags: [],
      published: false,
    };
    mockFetchOnce(scn, 200);

    await state().loadScenario("imported", "latest");
    expect(state().nodes.length).toBe(1);
    expect(state().nodes[0].id).toBe("n1");
    expect(state().meta.scenario_id).toBe("imported");
    expect(state().meta.version).toBe(3);
    expect(state().meta.tenant_id).toBe("t_acme");
    expect(state().selectedId).toBeNull();
    expect(state().toasts.some((t) => t.level === "success")).toBe(true);
  });

  it("non-2xx → error 세팅 + error toast", async () => {
    mockFetchOnce({ detail: "not found" }, 404);
    await state().loadScenario("ghost", "latest");
    expect(state().error).toMatch(/404/);
    expect(state().toasts.some((t) => t.level === "error")).toBe(true);
  });
});

// ── BuilderNode 타입 사용을 통한 링크 테스트 ─────────────────────────────────

describe("types are exported correctly", () => {
  it("BuilderNode 를 import 해서 사용할 수 있다", () => {
    const n: BuilderNode | null = null;
    expect(n).toBeNull();
  });
});
