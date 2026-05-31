/**
 * 빌더 전역 상태 스토어 — Zustand + immer-free 순수 함수 스타일.
 *
 * 설계:
 *   - React Flow 의 node/edge ref 기반 상태는 "복제 후 교체" 패턴으로 관리.
 *   - App 컴포넌트는 얇은 프레젠테이션 레이어 — 모든 도메인 로직은 store action 에 있다.
 *   - 저장/발행/로드 비동기 액션은 set() 으로 busy/error 를 명시 관리.
 *   - auth token 은 session 단위 localStorage 유지 (키 `agentoe.token`).
 *
 * 테스트 용 리셋:
 *   useBuilderStore.setState(initialState()) 로 깨끗한 상태 복원 가능.
 */
import { create } from "zustand";
import {
  addEdge as rfAddEdge,
  applyEdgeChanges,
  applyNodeChanges,
  type Connection,
  type EdgeChange,
  type NodeChange,
} from "reactflow";

import {
  getScenario,
  listScenarios,
  publishScenario,
  saveScenario,
  type ApiCredentials,
  type ScenarioListItem,
} from "@/lib/api";
import {
  defaultNodeConfig,
  fromGraph,
  toGraph,
  uniqueNodeId,
  validateGraph,
  type BuilderEdge,
  type BuilderNode,
  type GraphValidationIssue,
} from "@/lib/dsl";
import type {
  NodeType,
  Scenario,
  ScenarioNode as DslNode,
} from "@/types/scenario";

// ── 메타 ────────────────────────────────────────────────────────────────────

export interface BuilderMeta {
  scenario_id: string;
  tenant_id: string;
  name: string;
  description?: string;
  version: number;
  entry: string;
  fallback_node: string | null;
  published: boolean;
  tags: string[];
}

const DEFAULT_META: BuilderMeta = {
  scenario_id: "new_scenario_v1",
  tenant_id: "t_demo",
  name: "New scenario",
  version: 1,
  entry: "",
  fallback_node: null,
  published: false,
  tags: [],
};

// ── 에러/토큰 유틸 ────────────────────────────────────────────────────────────

const TOKEN_KEY = "agentoe.token";

function readToken(): string {
  if (typeof window === "undefined") return "";
  try {
    return window.localStorage.getItem(TOKEN_KEY) ?? "";
  } catch {
    return "";
  }
}

function writeToken(t: string): void {
  if (typeof window === "undefined") return;
  try {
    if (t) window.localStorage.setItem(TOKEN_KEY, t);
    else window.localStorage.removeItem(TOKEN_KEY);
  } catch {
    /* ignore quota / private mode */
  }
}

function formatError(e: unknown): string {
  if (e instanceof Error) return e.message;
  return String(e);
}

// ── 토스트 ───────────────────────────────────────────────────────────────────

export type ToastLevel = "info" | "success" | "error";

export interface Toast {
  id: string;
  level: ToastLevel;
  message: string;
  createdAt: number;
  /** 자동 소멸 ms — 0 이면 수동 dismiss 전용. */
  ttlMs: number;
}

// ── 스토어 상태/액션 시그니처 ────────────────────────────────────────────────

export interface BuilderState {
  nodes: BuilderNode[];
  edges: BuilderEdge[];
  meta: BuilderMeta;
  selectedId: string | null;
  token: string;
  busy: boolean;
  error: string | null;
  list: ScenarioListItem[];
  toasts: Toast[];

  // React Flow handlers
  onNodesChange: (chs: NodeChange[]) => void;
  onEdgesChange: (chs: EdgeChange[]) => void;
  onConnect: (c: Connection) => void;
  setSelectedId: (id: string | null) => void;

  // Domain actions
  addNodeFromPalette: (type: NodeType, position: { x: number; y: number }) => string;
  updateSelectedNode: (next: DslNode) => void;
  setEntryToSelected: () => void;
  toggleFallbackToSelected: () => void;
  setMeta: (patch: Partial<BuilderMeta>) => void;
  setToken: (t: string) => void;
  clearError: () => void;

  // Toast actions
  pushToast: (level: ToastLevel, message: string, ttlMs?: number) => string;
  dismissToast: (id: string) => void;

  // Async
  refreshList: () => Promise<void>;
  loadScenario: (scenarioId: string, version?: "latest" | "published" | number) => Promise<void>;
  saveCurrent: () => Promise<Scenario | null>;
  publishCurrent: () => Promise<void>;
}

// ── credentials 파생 ──────────────────────────────────────────────────────────

export function currentCredentials(state: BuilderState): ApiCredentials {
  return {
    token: state.token || null,
    tenantId: state.meta.tenant_id || null,
  };
}

// ── 스토어 구현 ────────────────────────────────────────────────────────────────

let _toastCounter = 0;
function _newToastId(): string {
  _toastCounter += 1;
  return `t_${Date.now()}_${_toastCounter}`;
}

export const useBuilderStore = create<BuilderState>((set, get) => ({
  nodes: [],
  edges: [],
  meta: { ...DEFAULT_META },
  selectedId: null,
  token: readToken(),
  busy: false,
  error: null,
  list: [],
  toasts: [],

  // ── React Flow handlers ────────────────────────────────────────────
  onNodesChange: (chs) =>
    set((s) => ({ nodes: applyNodeChanges(chs, s.nodes) as BuilderNode[] })),

  onEdgesChange: (chs) =>
    set((s) => ({ edges: applyEdgeChanges(chs, s.edges) as BuilderEdge[] })),

  onConnect: (c) =>
    set((s) => ({
      edges: rfAddEdge(
        {
          ...c,
          id: `e_${Date.now()}_${c.source}_${c.target}`,
          data: { when: undefined, label: undefined },
        } as BuilderEdge,
        s.edges,
      ) as BuilderEdge[],
    })),

  setSelectedId: (id) => set({ selectedId: id }),

  // ── Palette drop → 노드 추가 ───────────────────────────────────────
  addNodeFromPalette: (type, position) => {
    const state = get();
    const existing = new Set(state.nodes.map((n) => n.id));

    // start 노드 중복 방지: 이미 있으면 추가하지 않음
    if (type === "start" && state.nodes.some((n) => n.data.dsl.type === "start")) {
      state.pushToast("error", "Start 노드는 시나리오에 1개만 허용됩니다.", 3000);
      return "";
    }

    const id = uniqueNodeId(existing, type);
    const config = defaultNodeConfig(type);
    const dsl = { id, type, config } as DslNode;
    const node: BuilderNode = {
      id,
      type: "scenarioNode",
      position,
      data: { dsl },
    };

    // start 노드 추가 시 meta.entry 자동 설정
    if (type === "start") {
      set((s) => ({
        nodes: [...s.nodes, node],
        meta: { ...s.meta, entry: id },
      }));
    } else {
      set((s) => ({ nodes: [...s.nodes, node] }));
    }
    return id;
  },

  // ── PropertyPanel 에서 노드 편집 ───────────────────────────────────
  //
  // id 가 변경되면 React Flow 의 node.id, 모든 엣지 source/target, meta.entry/fallback_node
  // 4개 참조를 원자적으로 cascade 한다.
  updateSelectedNode: (next) => {
    const { selectedId } = get();
    if (!selectedId) return;
    const oldId = selectedId;
    const idChanged = next.id !== oldId;

    set((s) => {
      const nodes = s.nodes.map((n) =>
        n.id === oldId
          ? idChanged
            ? { ...n, id: next.id, data: { ...n.data, dsl: next } }
            : { ...n, data: { ...n.data, dsl: next } }
          : n,
      );
      const edges = idChanged
        ? s.edges.map((e) => ({
            ...e,
            source: e.source === oldId ? next.id : e.source,
            target: e.target === oldId ? next.id : e.target,
          }))
        : s.edges;
      const patchedMeta: BuilderMeta = !idChanged
        ? s.meta
        : {
            ...s.meta,
            entry: s.meta.entry === oldId ? next.id : s.meta.entry,
            fallback_node:
              s.meta.fallback_node === oldId ? next.id : s.meta.fallback_node,
          };
      return {
        nodes,
        edges,
        meta: patchedMeta,
        selectedId: idChanged ? next.id : s.selectedId,
      };
    });
  },

  setEntryToSelected: () => {
    const id = get().selectedId;
    if (!id) return;
    set((s) => ({ meta: { ...s.meta, entry: id } }));
  },

  toggleFallbackToSelected: () => {
    const id = get().selectedId;
    if (!id) return;
    set((s) => ({
      meta: {
        ...s.meta,
        fallback_node: s.meta.fallback_node === id ? null : id,
      },
    }));
  },

  setMeta: (patch) => set((s) => ({ meta: { ...s.meta, ...patch } })),

  setToken: (t) => {
    writeToken(t);
    set({ token: t });
  },

  clearError: () => set({ error: null }),

  // ── 토스트 ──────────────────────────────────────────────────────────
  pushToast: (level, message, ttlMs) => {
    const id = _newToastId();
    const effectiveTtl = ttlMs ?? (level === "error" ? 0 : 4000);
    const toast: Toast = {
      id,
      level,
      message,
      createdAt: Date.now(),
      ttlMs: effectiveTtl,
    };
    set((s) => ({ toasts: [...s.toasts, toast] }));
    if (effectiveTtl > 0) {
      // globalThis.setTimeout — 브라우저 / node / vitest fake timer 모두 호환.
      setTimeout(() => {
        // timer 가 도는 사이 이미 dismiss 되어 있을 수 있으므로 id 존재 체크 필요 없음
        set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) }));
      }, effectiveTtl);
    }
    return id;
  },

  dismissToast: (id) =>
    set((s) => ({ toasts: s.toasts.filter((t) => t.id !== id) })),

  // ── 비동기 ──────────────────────────────────────────────────────────
  refreshList: async () => {
    try {
      const items = await listScenarios(currentCredentials(get()));
      set({ list: items });
    } catch {
      // 비로그인 상태 / 백엔드 미가동 — 조용히 빈 목록
      set({ list: [] });
    }
  },

  loadScenario: async (scenarioId, version = "latest") => {
    set({ busy: true, error: null });
    try {
      const s = await getScenario(scenarioId, version, currentCredentials(get()));
      const g = toGraph(s);
      set({
        nodes: g.nodes,
        edges: g.edges,
        meta: {
          scenario_id: s.scenario_id,
          tenant_id: s.tenant_id,
          name: s.name,
          description: s.description,
          version: s.version,
          entry: s.entry,
          fallback_node: s.fallback_node ?? null,
          published: s.published,
          tags: s.tags,
        },
        selectedId: null,
      });
      get().pushToast(
        "success",
        `"${s.name}" v${s.version} 불러왔습니다 (${s.nodes.length} 노드, ${s.edges.length} 엣지)`,
      );
    } catch (e) {
      const msg = formatError(e);
      set({ error: msg });
      get().pushToast("error", `불러오기 실패: ${msg}`);
    } finally {
      set({ busy: false });
    }
  },

  saveCurrent: async () => {
    const assembled = assembleScenario(get());
    if (!assembled.scenario) {
      const msg = assembled.error ?? "DSL invalid";
      set({ error: msg });
      get().pushToast("error", `저장 차단: ${msg}`);
      return null;
    }
    set({ busy: true, error: null });
    try {
      const saved = await saveScenario(assembled.scenario, currentCredentials(get()));
      // 반환된 서버 채번 version 을 메타에 반영 (draft 로 저장됨)
      set((s) => ({
        meta: { ...s.meta, version: saved.version, published: false },
      }));
      await get().refreshList();
      get().pushToast("success", `저장 성공 · v${saved.version} (draft)`);
      return saved;
    } catch (e) {
      const msg = formatError(e);
      set({ error: msg });
      get().pushToast("error", `저장 실패: ${msg}`);
      return null;
    } finally {
      set({ busy: false });
    }
  },

  publishCurrent: async () => {
    const assembled = assembleScenario(get());
    if (!assembled.scenario) {
      const msg = assembled.error ?? "DSL invalid";
      set({ error: msg });
      get().pushToast("error", `발행 차단: ${msg}`);
      return;
    }
    set({ busy: true, error: null });
    try {
      await publishScenario(
        assembled.scenario.scenario_id,
        assembled.scenario.version,
        currentCredentials(get()),
      );
      set((s) => ({ meta: { ...s.meta, published: true } }));
      await get().refreshList();
      get().pushToast(
        "success",
        `발행 완료 · ${assembled.scenario.scenario_id} v${assembled.scenario.version}`,
      );
    } catch (e) {
      const msg = formatError(e);
      set({ error: msg });
      get().pushToast("error", `발행 실패: ${msg}`);
    } finally {
      set({ busy: false });
    }
  },
}));

// ── Selector 헬퍼 ──────────────────────────────────────────────────────────────

export function assembleScenario(state: BuilderState): {
  scenario: Scenario | null;
  error: string | null;
} {
  try {
    const scenario = fromGraph({
      graph: { nodes: state.nodes, edges: state.edges },
      meta: {
        scenario_id: state.meta.scenario_id,
        tenant_id: state.meta.tenant_id,
        version: state.meta.version,
        name: state.meta.name,
        description: state.meta.description,
        entry: state.meta.entry,
        fallback_node: state.meta.fallback_node ?? null,
        tags: state.meta.tags,
        published: state.meta.published,
        limits: {
          max_turns: 30,
          max_duration_s: 900,
          max_tool_calls_per_turn: 3,
          max_cost_cents_per_session: 50.0,
        },
      },
    });
    return { scenario, error: null };
  } catch (e) {
    return { scenario: null, error: formatError(e) };
  }
}

/** 현재 선택된 노드의 DSL — 없으면 null. */
export function selectSelectedDsl(state: BuilderState): DslNode | null {
  if (!state.selectedId) return null;
  const n = state.nodes.find((x) => x.id === state.selectedId);
  return n?.data.dsl ?? null;
}

/** entry/fallback highlight 가 주입된 노드 목록. */
export function selectHighlightedNodes(state: BuilderState): BuilderNode[] {
  const { entry, fallback_node } = state.meta;
  return state.nodes.map((n) => ({
    ...n,
    data: {
      ...n.data,
      highlight:
        n.id === entry
          ? ("entry" as const)
          : n.id === fallback_node
            ? ("fallback" as const)
            : null,
    },
  }));
}

/** 현재 그래프의 검증 이슈. (validateGraph + zod schema 에러). */
export function selectIssues(state: BuilderState): GraphValidationIssue[] {
  const a = assembleScenario(state);
  if (!a.scenario) {
    return [
      {
        severity: "error",
        code: "SCHEMA",
        message: a.error ?? "unknown",
      },
    ];
  }
  return validateGraph(a.scenario);
}

// ── 테스트 친화: 초기 상태 reset helper ───────────────────────────────────────

export function resetBuilderStoreForTest(): void {
  useBuilderStore.setState({
    nodes: [],
    edges: [],
    meta: { ...DEFAULT_META },
    selectedId: null,
    token: "",
    busy: false,
    error: null,
    list: [],
    toasts: [],
  });
}
