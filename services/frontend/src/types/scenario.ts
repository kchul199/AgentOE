/**
 * AgentOE Scenario DSL — TypeScript 타입 + Zod 검증 스키마.
 *
 * backend/app/agentic/scenario_dsl.py (Pydantic v2) 와 1:1 대응.
 * 이 파일의 수정은 반드시 백엔드 DSL 과 동시에 이루어져야 한다.
 *
 * 검증 정책:
 *   - id 패턴: ^[a-zA-Z0-9_\-]+$, 길이 1~64
 *   - scenario_id: ^[a-z0-9_\-]+$ (소문자 전용)
 *   - 8개 노드 타입: intent | llm | tool | branch | transfer | wait | context | end
 *   - Edge: from/to 필수, when/label 선택. wire 포맷에서 키는 "from".
 *   - 도달 가능성/중복 id 는 백엔드 전용 검증 — 프런트는 submit 전 zod 구문 검증까지.
 */
import { z } from "zod";

// ── 공통 규칙 ───────────────────────────────────────────────────────────────

const NODE_ID = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-zA-Z0-9_\-]+$/u, "id must match ^[a-zA-Z0-9_\\-]+$");

const SCENARIO_ID = z
  .string()
  .min(1)
  .max(64)
  .regex(/^[a-z0-9_\-]+$/u, "scenario_id must match ^[a-z0-9_\\-]+$");

const NODE_BASE = {
  id: NODE_ID,
  label: z.string().optional(),
  description: z.string().optional(),
};

// ── 8개 노드 Config ─────────────────────────────────────────────────────────

export const IntentNodeConfigSchema = z.object({
  labels: z.array(z.string()).min(2),
  model: z.string().default("groq-llama-3.3-70b"),
  prompt_template: z.string().optional(),
  threshold: z.number().default(0.5),
});

export const LLMNodeConfigSchema = z.object({
  model: z.string().default("groq-llama-4-scout"),
  fallback_model: z.string().default("groq-llama-3.3-70b"),
  system_prompt: z.string(),
  prompt_template: z.string().optional(),
  temperature: z.number().default(0.7),
  max_tokens: z.number().int().default(512),
  streaming: z.boolean().default(true),
  enable_filler: z.boolean().default(true),
});

export const ToolNodeConfigSchema = z.object({
  tool_name: z.string().min(1),
  args_template: z.record(z.string(), z.string()).default({}),
  timeout_s: z.number().default(5.0),
  retry: z.number().int().default(1),
  on_error: z.enum(["fallback", "raise", "continue"]).default("fallback"),
});

export const BranchNodeConfigSchema = z.object({
  mode: z.enum(["expr", "intent", "slot"]).default("intent"),
  slot_key: z.string().optional(),
});

export const TransferNodeConfigSchema = z.object({
  queue: z.string().default("default"),
  reason: z.string().default("user_request"),
  include_summary: z.boolean().default(true),
});

export const WaitNodeConfigSchema = z.object({
  timeout_s: z.number().default(15.0),
  prompt_on_timeout: z
    .string()
    .nullable()
    .default("죄송합니다, 말씀 못 들었습니다. 다시 말씀해 주세요."),
});

export const ContextUpdateNodeConfigSchema = z.object({
  set_slots: z.record(z.string(), z.unknown()).default({}),
  clear_slots: z.array(z.string()).default([]),
});

export const EndNodeConfigSchema = z.object({
  closing_message: z.string().nullable().optional(),
});

// ── 노드 Tagged Union ───────────────────────────────────────────────────────

export const NodeSchema = z.discriminatedUnion("type", [
  z.object({ ...NODE_BASE, type: z.literal("intent"), config: IntentNodeConfigSchema }),
  z.object({ ...NODE_BASE, type: z.literal("llm"), config: LLMNodeConfigSchema }),
  z.object({ ...NODE_BASE, type: z.literal("tool"), config: ToolNodeConfigSchema }),
  z.object({ ...NODE_BASE, type: z.literal("branch"), config: BranchNodeConfigSchema }),
  z.object({ ...NODE_BASE, type: z.literal("transfer"), config: TransferNodeConfigSchema }),
  z.object({ ...NODE_BASE, type: z.literal("wait"), config: WaitNodeConfigSchema }),
  z.object({ ...NODE_BASE, type: z.literal("context"), config: ContextUpdateNodeConfigSchema }),
  z.object({ ...NODE_BASE, type: z.literal("end"), config: EndNodeConfigSchema.default({}) }),
]);

export type ScenarioNode = z.infer<typeof NodeSchema>;
export type NodeType = ScenarioNode["type"];

// ── Edge ────────────────────────────────────────────────────────────────────

/**
 * wire 포맷 (백엔드와 주고받는 JSON): { "from": "...", "to": "...", ... }
 * 내부 포맷 (TS 변수): { from: "...", to: "...", ... } — 키 이름 그대로 쓰되
 * 백엔드의 alias="from" 과 일치하게 직렬화 시 항상 "from" 키를 쓴다.
 */
export const EdgeSchema = z.object({
  from: z.string().min(1),
  to: z.string().min(1),
  when: z.string().nullable().optional(),
  label: z.string().nullable().optional(),
});

export type Edge = z.infer<typeof EdgeSchema>;

// ── ScenarioLimits ──────────────────────────────────────────────────────────

export const ScenarioLimitsSchema = z.object({
  max_turns: z.number().int().min(1).max(200).default(30),
  max_duration_s: z.number().int().min(10).max(3600).default(900),
  max_tool_calls_per_turn: z.number().int().min(0).max(10).default(3),
  max_cost_cents_per_session: z.number().min(0).default(50.0),
});

export type ScenarioLimits = z.infer<typeof ScenarioLimitsSchema>;

// ── Scenario ────────────────────────────────────────────────────────────────

export const ScenarioSchema = z
  .object({
    scenario_id: SCENARIO_ID,
    tenant_id: z.string().min(1).max(64),
    version: z.number().int().min(1).default(1),
    name: z.string().min(1),
    description: z.string().optional(),

    entry: z.string().min(1),
    fallback_node: z.string().nullable().optional(),

    nodes: z.array(NodeSchema),
    edges: z.array(EdgeSchema),
    limits: ScenarioLimitsSchema.default({
      max_turns: 30,
      max_duration_s: 900,
      max_tool_calls_per_turn: 3,
      max_cost_cents_per_session: 50.0,
    }),

    tags: z.array(z.string()).default([]),
    created_by: z.string().nullable().optional(),
    created_at: z.string().nullable().optional(),
    published: z.boolean().default(false),
  })
  .strict();

export type Scenario = z.infer<typeof ScenarioSchema>;

/** 노드 팔레트 메타 — 빌더 UI 에서 drag source 로 쓴다. */
export interface NodePaletteEntry {
  type: NodeType;
  label: string;
  description: string;
  color: string;
}

export const NODE_PALETTE: NodePaletteEntry[] = [
  { type: "intent",   label: "Intent",   description: "사용자 발화 의도 분류",      color: "#6366f1" },
  { type: "llm",      label: "LLM",      description: "LLM 응답 생성 (스트리밍)",  color: "#0ea5e9" },
  { type: "tool",     label: "Tool",     description: "외부 커넥터/API 호출",       color: "#f59e0b" },
  { type: "branch",   label: "Branch",   description: "조건 분기",                  color: "#a855f7" },
  { type: "transfer", label: "Transfer", description: "상담원 이관 (SIP REFER)",    color: "#ef4444" },
  { type: "wait",     label: "Wait",     description: "사용자 발화 대기",           color: "#14b8a6" },
  { type: "context",  label: "Context",  description: "슬롯/메모리 업데이트",       color: "#84cc16" },
  { type: "end",      label: "End",      description: "세션 종료",                  color: "#64748b" },
];
