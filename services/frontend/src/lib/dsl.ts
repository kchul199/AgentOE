/**
 * React Flow 그래프 ↔ Scenario DSL 양방향 변환 + 검증.
 *
 * 계약:
 *   1. 라운드트립 손실 금지 — toGraph(fromGraph(g)) 의 nodes/edges 는 동일 id 집합과
 *      config 객체 그대로 유지. 좌표(position)는 별도 ui/* 메타에 저장.
 *   2. wire 포맷 (백엔드와 주고받는 JSON) 의 edge 키는 항상 "from" (예약어 대응).
 *      TS Edge 는 from/to 둘 다 노출되지만 scoped 로 alias 충돌 방지.
 *   3. 노드 position 은 DSL 에 포함하지 않음. `metadata.ui_positions` 맵(id → {x,y})
 *      형태로 별도 필드에 저장 — 백엔드가 알 필요 없이 프런트에서 통과시키는 용도.
 */
import type { Edge as RfEdge, Node as RfNode } from "reactflow";

import {
  type Edge,
  EdgeSchema,
  NODE_PALETTE,
  NodeSchema,
  type NodeType,
  type Scenario,
  ScenarioSchema,
  type ScenarioNode,
} from "@/types/scenario";

// ── React Flow 노드/엣지 메타 ──────────────────────────────────────────────

export interface RfNodeData {
  /** DSL 기준 도메인 노드 — NodeSchema 가 보장하는 형태 그대로. */
  dsl: ScenarioNode;
}

/** React Flow 의 generic 사용을 간결하게 하기 위한 alias. */
export type BuilderNode = RfNode<RfNodeData>;
export type BuilderEdge = RfEdge<{ when?: string; label?: string }>;

export interface BuilderGraph {
  nodes: BuilderNode[];
  edges: BuilderEdge[];
}

// ── 기본 config 팩토리 ─────────────────────────────────────────────────────

export function defaultNodeConfig(type: NodeType): ScenarioNode["config"] {
  switch (type) {
    case "intent":
      return { labels: ["billing", "default"], model: "groq-llama-3.3-70b", threshold: 0.5 };
    case "llm":
      return {
        model: "groq-llama-4-scout",
        fallback_model: "groq-llama-3.3-70b",
        system_prompt: "",
        temperature: 0.7,
        max_tokens: 512,
        streaming: true,
        enable_filler: true,
      };
    case "tool":
      return {
        tool_name: "",
        args_template: {},
        timeout_s: 5.0,
        retry: 1,
        on_error: "fallback",
      };
    case "branch":
      return { mode: "intent" };
    case "transfer":
      return { queue: "default", reason: "user_request", include_summary: true };
    case "wait":
      return {
        timeout_s: 15.0,
        prompt_on_timeout: "죄송합니다, 말씀 못 들었습니다. 다시 말씀해 주세요.",
      };
    case "context":
      return { set_slots: {}, clear_slots: [] };
    case "end":
      return { closing_message: null };
    default: {
      // exhaustive
      const _never: never = type;
      throw new Error(`unreachable node type: ${String(_never)}`);
    }
  }
}

/** 고유한 id 자동 생성 — 팔레트 label 기반 + 증가 카운터. */
export function uniqueNodeId(existing: Set<string>, type: NodeType): string {
  const base = type;
  let i = 1;
  while (existing.has(`${base}_${i}`)) i++;
  return `${base}_${i}`;
}

export function paletteColor(type: NodeType): string {
  const e = NODE_PALETTE.find((p) => p.type === type);
  return e ? e.color : "#64748b";
}

// ── 변환: Scenario DSL → React Flow 그래프 ─────────────────────────────────

export interface ToGraphOptions {
  /** id → {x,y} 위치 맵 (ui 메타). 누락 시 grid 기본 레이아웃 사용. */
  positions?: Record<string, { x: number; y: number }>;
}

export function toGraph(scenario: Scenario, opts: ToGraphOptions = {}): BuilderGraph {
  const positions = opts.positions ?? {};
  const nodes: BuilderNode[] = scenario.nodes.map((n, idx) => {
    const pos = positions[n.id] ?? { x: 120 + (idx % 4) * 240, y: 80 + Math.floor(idx / 4) * 160 };
    return {
      id: n.id,
      type: "scenarioNode",
      position: pos,
      data: { dsl: n },
    };
  });

  const edges: BuilderEdge[] = scenario.edges.map((e, idx) => ({
    id: `e_${idx}_${e.from}_${e.to}_${e.when ?? "_"}`,
    source: e.from,
    target: e.to,
    label: e.label ?? (e.when ? `when: ${e.when}` : undefined),
    data: {
      when: e.when ?? undefined,
      label: e.label ?? undefined,
    },
  }));

  return { nodes, edges };
}

// ── 변환: React Flow 그래프 → Scenario DSL ─────────────────────────────────

export interface FromGraphInput {
  graph: BuilderGraph;
  meta: Omit<Scenario, "nodes" | "edges"> | Partial<Scenario>;
}

export function fromGraph(input: FromGraphInput): Scenario {
  const { graph, meta } = input;

  const nodes: ScenarioNode[] = graph.nodes.map((n) => n.data.dsl);
  const edges: Edge[] = graph.edges.map((e) => ({
    from: e.source,
    to: e.target,
    when: e.data?.when ?? undefined,
    label: e.data?.label ?? undefined,
  }));

  // 기본값 채우기 후 zod 검증까지 통과시킨다.
  const draft = {
    ...meta,
    nodes,
    edges,
  };
  return ScenarioSchema.parse(draft);
}

// ── 얇은 검증 (백엔드 전송 전) ─────────────────────────────────────────────

export interface GraphValidationIssue {
  severity: "error" | "warning";
  code: string;
  message: string;
  node_id?: string;
  edge_index?: number;
}

/**
 * 백엔드 호출 이전에 프런트에서 빠르게 잡을 수 있는 구조 오류 수집.
 *
 *   - 중복 id
 *   - entry 지정/존재
 *   - edge 양끝 노드 존재
 *   - entry 에서 도달 불가 노드 (fallback_node 는 예외)
 *
 * 반환된 error 가 0 건이면 백엔드 POST /scenarios 로 안전하게 보낸다.
 */
export function validateGraph(scenario: Scenario): GraphValidationIssue[] {
  const issues: GraphValidationIssue[] = [];
  const idSet = new Set<string>();

  for (const n of scenario.nodes) {
    if (idSet.has(n.id)) {
      issues.push({
        severity: "error",
        code: "DUPLICATE_NODE_ID",
        message: `중복된 노드 id: ${n.id}`,
        node_id: n.id,
      });
    }
    idSet.add(n.id);
  }

  if (!idSet.has(scenario.entry)) {
    issues.push({
      severity: "error",
      code: "ENTRY_MISSING",
      message: `entry 노드 '${scenario.entry}' 가 그래프에 없습니다`,
    });
  }
  if (scenario.fallback_node && !idSet.has(scenario.fallback_node)) {
    issues.push({
      severity: "error",
      code: "FALLBACK_MISSING",
      message: `fallback_node '${scenario.fallback_node}' 가 그래프에 없습니다`,
    });
  }

  scenario.edges.forEach((e, idx) => {
    if (!idSet.has(e.from)) {
      issues.push({
        severity: "error",
        code: "EDGE_FROM_MISSING",
        message: `엣지 from='${e.from}' 가 그래프에 없습니다`,
        edge_index: idx,
      });
    }
    if (!idSet.has(e.to)) {
      issues.push({
        severity: "error",
        code: "EDGE_TO_MISSING",
        message: `엣지 to='${e.to}' 가 그래프에 없습니다`,
        edge_index: idx,
      });
    }
  });

  // 도달성 체크 (entry 기준 BFS)
  if (idSet.has(scenario.entry)) {
    const reachable = new Set<string>([scenario.entry]);
    let changed = true;
    while (changed) {
      changed = false;
      for (const e of scenario.edges) {
        if (reachable.has(e.from) && !reachable.has(e.to)) {
          reachable.add(e.to);
          changed = true;
        }
      }
    }
    for (const id of idSet) {
      if (id === scenario.fallback_node) continue; // 동적 진입 허용
      if (!reachable.has(id)) {
        issues.push({
          severity: "warning",
          code: "UNREACHABLE_NODE",
          message: `entry 에서 도달 불가: ${id}`,
          node_id: id,
        });
      }
    }
  }

  return issues;
}

// ── re-export 편의 ─────────────────────────────────────────────────────────

export { EdgeSchema, NodeSchema, ScenarioSchema };
