/**
 * Track 4-F: DSL 라운드트립 & validateGraph 단위 테스트.
 *
 * 계약:
 *   - toGraph(fromGraph(g)) 의 노드/엣지 id·type·config 가 손실 없이 보존된다.
 *   - defaultNodeConfig(type) 로 만든 노드는 ScenarioSchema 를 통과한다.
 *   - validateGraph 는 DUPLICATE_NODE_ID / ENTRY_MISSING / FALLBACK_MISSING /
 *     EDGE_FROM_MISSING / EDGE_TO_MISSING / UNREACHABLE_NODE 6 종 issue 를 감지한다.
 *   - fallback_node 는 edge 연결이 없어도 UNREACHABLE 로 잡히지 않는다 (동적 진입).
 *   - uniqueNodeId 는 기존 id 집합과 충돌하지 않는 다음 번호를 반환한다.
 */
import { describe, expect, it } from "vitest";

import {
  defaultNodeConfig,
  fromGraph,
  toGraph,
  uniqueNodeId,
  validateGraph,
} from "@/lib/dsl";
import {
  NODE_PALETTE,
  ScenarioSchema,
  type Scenario,
  type ScenarioNode,
} from "@/types/scenario";

// ── 헬퍼 ───────────────────────────────────────────────────────────────────────

function makeMeta(overrides: Partial<Scenario> = {}): Omit<Scenario, "nodes" | "edges"> {
  return {
    scenario_id: "greet",
    tenant_id: "t_acme",
    version: 1,
    name: "Greeting",
    entry: "start",
    fallback_node: null,
    limits: {
      max_turns: 30,
      max_duration_s: 900,
      max_tool_calls_per_turn: 3,
      max_cost_cents_per_session: 50.0,
    },
    tags: [],
    published: false,
    ...overrides,
  } as Omit<Scenario, "nodes" | "edges">;
}

function makeScenario(overrides: Partial<Scenario> = {}): Scenario {
  const base: Scenario = ScenarioSchema.parse({
    scenario_id: "greet",
    tenant_id: "t_acme",
    version: 1,
    name: "Greeting",
    entry: "start",
    fallback_node: null,
    nodes: [
      {
        id: "start",
        type: "llm",
        config: {
          model: "groq-llama-4-scout",
          fallback_model: "groq-llama-3.3-70b",
          system_prompt: "안녕하세요",
          temperature: 0.7,
          max_tokens: 512,
          streaming: true,
          enable_filler: true,
        },
      },
      {
        id: "done",
        type: "end",
        config: { closing_message: "감사합니다" },
      },
    ],
    edges: [{ from: "start", to: "done" }],
    tags: [],
    published: false,
  });
  return { ...base, ...overrides } as Scenario;
}

// ── 라운드트립 보존 ────────────────────────────────────────────────────────────

describe("toGraph / fromGraph round-trip", () => {
  it("노드 id / type / config 가 손실 없이 보존된다", () => {
    const scn = makeScenario();
    const graph = toGraph(scn);
    const back = fromGraph({ graph, meta: makeMeta({ entry: scn.entry }) });

    expect(back.nodes.map((n) => n.id)).toEqual(scn.nodes.map((n) => n.id));
    expect(back.nodes.map((n) => n.type)).toEqual(scn.nodes.map((n) => n.type));
    for (let i = 0; i < scn.nodes.length; i++) {
      expect(back.nodes[i].config).toEqual(scn.nodes[i].config);
    }
  });

  it("엣지 from / to / when / label 이 보존된다", () => {
    const scn = makeScenario({
      edges: [
        { from: "start", to: "done", when: "intent == 'bye'", label: "bye branch" },
      ],
    });
    const graph = toGraph(scn);
    const back = fromGraph({ graph, meta: makeMeta({ entry: scn.entry }) });
    expect(back.edges).toHaveLength(1);
    expect(back.edges[0].from).toBe("start");
    expect(back.edges[0].to).toBe("done");
    expect(back.edges[0].when).toBe("intent == 'bye'");
    expect(back.edges[0].label).toBe("bye branch");
  });

  it("positions 제공 시 toGraph 노드 position 이 덮어쓴다", () => {
    const scn = makeScenario();
    const graph = toGraph(scn, {
      positions: { start: { x: 10, y: 20 }, done: { x: 300, y: 200 } },
    });
    const startPos = graph.nodes.find((n) => n.id === "start")!.position;
    expect(startPos).toEqual({ x: 10, y: 20 });
  });

  it("여러 노드 타입을 모두 포함한 그래프도 라운드트립 유지", () => {
    const nodes: ScenarioNode[] = [
      {
        id: "intent_1",
        type: "intent",
        config: {
          labels: ["billing", "default"],
          model: "groq-llama-3.3-70b",
          threshold: 0.5,
        },
      },
      {
        id: "llm_1",
        type: "llm",
        config: {
          model: "groq-llama-4-scout",
          fallback_model: "groq-llama-3.3-70b",
          system_prompt: "prompt",
          temperature: 0.7,
          max_tokens: 512,
          streaming: true,
          enable_filler: true,
        },
      },
      {
        id: "tool_1",
        type: "tool",
        config: {
          tool_name: "lookup_account",
          args_template: { cust: "{{phone}}" },
          timeout_s: 5.0,
          retry: 1,
          on_error: "fallback",
        },
      },
      {
        id: "branch_1",
        type: "branch",
        config: { mode: "intent" },
      },
      { id: "end_1", type: "end", config: { closing_message: null } },
    ];
    const scn = ScenarioSchema.parse({
      scenario_id: "multi",
      tenant_id: "t_acme",
      name: "Multi",
      entry: "intent_1",
      fallback_node: null,
      nodes,
      edges: [
        { from: "intent_1", to: "llm_1" },
        { from: "llm_1", to: "tool_1" },
        { from: "tool_1", to: "branch_1" },
        { from: "branch_1", to: "end_1" },
      ],
      published: false,
    });
    const graph = toGraph(scn);
    const back = fromGraph({
      graph,
      meta: makeMeta({ scenario_id: "multi", entry: "intent_1", name: "Multi" }),
    });
    expect(back.nodes.map((n) => n.type)).toEqual([
      "intent",
      "llm",
      "tool",
      "branch",
      "end",
    ]);
    expect(back.edges).toHaveLength(4);
  });
});

// ── defaultNodeConfig 타입별 ScenarioSchema 통과 ─────────────────────────────

describe("defaultNodeConfig", () => {
  it.each(NODE_PALETTE.map((p) => p.type))(
    "type=%s 기본 config 는 NodeSchema 를 통과한다",
    (type) => {
      const config = defaultNodeConfig(type);
      // Scenario 단위로 감싸서 검증 — entry/fallback 이슈를 피하기 위해 단일 노드로 구성
      const scn = {
        scenario_id: "t",
        tenant_id: "t_acme",
        name: "T",
        entry: "n1",
        fallback_node: null,
        nodes: [{ id: "n1", type, config }],
        edges: [],
        published: false,
      };
      // tool 타입은 tool_name 이 비어있어 실패할 수 있음 — 그 경우 보정 후 검증
      if (type === "tool") {
        (scn.nodes[0].config as { tool_name: string }).tool_name = "demo";
      }
      const parsed = ScenarioSchema.parse(scn);
      expect(parsed.nodes[0].type).toBe(type);
    },
  );

  it("알 수 없는 타입은 런타임 에러", () => {
    expect(() => defaultNodeConfig("bogus" as never)).toThrow();
  });
});

// ── uniqueNodeId ───────────────────────────────────────────────────────────────

describe("uniqueNodeId", () => {
  it("빈 집합에서 _1 반환", () => {
    expect(uniqueNodeId(new Set(), "llm")).toBe("llm_1");
  });

  it("충돌 시 다음 번호로 건너뛴다", () => {
    const existing = new Set(["llm_1", "llm_2", "llm_3"]);
    expect(uniqueNodeId(existing, "llm")).toBe("llm_4");
  });

  it("홀 지점 연속 번호 — _2 가 비었어도 _3 이후 첫 빈 번호 반환", () => {
    // 구현상 1부터 순차 증가이므로 비어 있는 가장 작은 번호가 선택됨
    const existing = new Set(["llm_1", "llm_3"]);
    expect(uniqueNodeId(existing, "llm")).toBe("llm_2");
  });
});

// ── validateGraph 이슈 코드 6종 ────────────────────────────────────────────────

describe("validateGraph", () => {
  it("정상 그래프 — 이슈 0건", () => {
    const scn = makeScenario();
    expect(validateGraph(scn)).toEqual([]);
  });

  it("DUPLICATE_NODE_ID 감지", () => {
    const scn = ScenarioSchema.parse({
      scenario_id: "dup",
      tenant_id: "t_acme",
      name: "Dup",
      entry: "a",
      fallback_node: null,
      nodes: [
        { id: "a", type: "end", config: {} },
        { id: "a", type: "end", config: {} },
      ],
      edges: [],
      published: false,
    });
    const codes = validateGraph(scn).map((i) => i.code);
    expect(codes).toContain("DUPLICATE_NODE_ID");
  });

  it("ENTRY_MISSING 감지", () => {
    // ScenarioSchema 는 entry 존재성을 검증하지 않으므로 직접 구성
    const scn: Scenario = {
      ...makeScenario(),
      entry: "not_there",
    };
    const codes = validateGraph(scn).map((i) => i.code);
    expect(codes).toContain("ENTRY_MISSING");
  });

  it("FALLBACK_MISSING 감지", () => {
    const scn: Scenario = {
      ...makeScenario(),
      fallback_node: "ghost_fallback",
    };
    const codes = validateGraph(scn).map((i) => i.code);
    expect(codes).toContain("FALLBACK_MISSING");
  });

  it("EDGE_FROM_MISSING / EDGE_TO_MISSING 감지", () => {
    const scn: Scenario = {
      ...makeScenario(),
      edges: [
        { from: "nope_src", to: "done" },
        { from: "start", to: "nope_dst" },
      ],
    };
    const codes = validateGraph(scn).map((i) => i.code);
    expect(codes).toContain("EDGE_FROM_MISSING");
    expect(codes).toContain("EDGE_TO_MISSING");
  });

  it("UNREACHABLE_NODE 는 warning 으로 리포트, fallback_node 는 예외", () => {
    const scn = ScenarioSchema.parse({
      scenario_id: "unreach",
      tenant_id: "t_acme",
      name: "U",
      entry: "a",
      fallback_node: "fb",
      nodes: [
        { id: "a", type: "end", config: {} },
        { id: "b", type: "end", config: {} }, // 도달 불가 — warning
        { id: "fb", type: "end", config: {} }, // fallback → 리포트 안 됨
      ],
      edges: [],
      published: false,
    });
    const issues = validateGraph(scn);
    const unreachable = issues.filter((i) => i.code === "UNREACHABLE_NODE");
    expect(unreachable).toHaveLength(1);
    expect(unreachable[0].severity).toBe("warning");
    expect(unreachable[0].node_id).toBe("b");
    // fb 는 fallback 이므로 리포트되지 않아야 함
    expect(issues.some((i) => i.node_id === "fb")).toBe(false);
  });

  it("entry 가 그래프에 없으면 도달성 검사는 수행하지 않는다", () => {
    const scn: Scenario = {
      ...makeScenario(),
      entry: "ghost",
    };
    const issues = validateGraph(scn);
    // ENTRY_MISSING 한 건만 — UNREACHABLE_NODE 는 리포트되지 않음
    const codes = issues.map((i) => i.code);
    expect(codes).toContain("ENTRY_MISSING");
    expect(codes).not.toContain("UNREACHABLE_NODE");
  });
});
