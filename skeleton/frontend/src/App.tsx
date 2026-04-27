/**
 * Scenario Builder 루트 — 툴바 + 팔레트 + React Flow 캔버스 + 속성/검증 사이드바.
 *
 * Zustand 스토어 consumer 전용 — 모든 도메인 상태/액션은 `@/store/builderStore` 에 있다.
 * 이 컴포넌트는 프레젠테이션만 담당:
 *   - UI 이벤트를 스토어 액션에 배선
 *   - store selector 로부터 얻은 값을 렌더
 *   - 로컬 state 는 "목록 모달 열림", "token input focus" 처럼 컴포넌트 전용 UI state 에 한함
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  Controls,
  MiniMap,
  type OnSelectionChangeParams,
  type XYPosition,
} from "reactflow";

import Palette from "@/components/Palette";
import PropertyPanel from "@/components/PropertyPanel";
import ScenarioNode from "@/components/ScenarioNode";
import ToastStack from "@/components/ToastStack";
import ValidationPanel from "@/components/ValidationPanel";
import type { NodeType } from "@/types/scenario";
import {
  assembleScenario,
  selectHighlightedNodes,
  selectIssues,
  selectSelectedDsl,
  useBuilderStore,
} from "@/store/builderStore";

const NODE_TYPES = { scenarioNode: ScenarioNode };

export default function App(): JSX.Element {
  // ── 스토어 구독 (selector 단위) ─────────────────────────────────────
  const edges = useBuilderStore((s) => s.edges);
  const meta = useBuilderStore((s) => s.meta);
  const selectedId = useBuilderStore((s) => s.selectedId);
  const busy = useBuilderStore((s) => s.busy);
  const error = useBuilderStore((s) => s.error);
  const list = useBuilderStore((s) => s.list);
  const token = useBuilderStore((s) => s.token);

  // 복합 파생 값 — useBuilderStore(selector) 는 얕은 비교 사용 (Object.is).
  // highlightedNodes / issues / assembled 는 nodes/edges/meta 가 바뀔 때마다 재계산되므로
  // 각각 의존 필드만 구독 후 useMemo 로 계산한다.
  const nodes = useBuilderStore((s) => s.nodes);
  const highlightedNodes = useMemo(
    () => selectHighlightedNodes({ ...useBuilderStore.getState(), nodes, meta }),
    [nodes, meta],
  );
  const assembled = useMemo(
    () => assembleScenario({ ...useBuilderStore.getState(), nodes, edges, meta }),
    [nodes, edges, meta],
  );
  const issues = useMemo(
    () => selectIssues({ ...useBuilderStore.getState(), nodes, edges, meta }),
    [nodes, edges, meta],
  );
  const selectedDsl = useMemo(
    () => selectSelectedDsl({ ...useBuilderStore.getState(), nodes, selectedId }),
    [nodes, selectedId],
  );

  // ── 스토어 액션 ─────────────────────────────────────────────────────
  const onNodesChange = useBuilderStore((s) => s.onNodesChange);
  const onEdgesChange = useBuilderStore((s) => s.onEdgesChange);
  const onConnect = useBuilderStore((s) => s.onConnect);
  const setSelectedId = useBuilderStore((s) => s.setSelectedId);
  const addNodeFromPalette = useBuilderStore((s) => s.addNodeFromPalette);
  const updateSelectedNode = useBuilderStore((s) => s.updateSelectedNode);
  const setEntryToSelected = useBuilderStore((s) => s.setEntryToSelected);
  const toggleFallbackToSelected = useBuilderStore((s) => s.toggleFallbackToSelected);
  const setMeta = useBuilderStore((s) => s.setMeta);
  const setToken = useBuilderStore((s) => s.setToken);
  const clearError = useBuilderStore((s) => s.clearError);
  const refreshList = useBuilderStore((s) => s.refreshList);
  const loadScenario = useBuilderStore((s) => s.loadScenario);
  const saveCurrent = useBuilderStore((s) => s.saveCurrent);
  const publishCurrent = useBuilderStore((s) => s.publishCurrent);

  // ── 로컬 UI state (목록 모달 열림) ─────────────────────────────────
  const [showList, setShowList] = useState(false);

  // ── React Flow 이벤트 ────────────────────────────────────────────────
  const onSelectionChange = useCallback(
    (p: OnSelectionChangeParams) => {
      setSelectedId(p.nodes[0]?.id ?? null);
    },
    [setSelectedId],
  );

  // ── Drag & Drop (palette → canvas) ───────────────────────────────────
  const onDragOver = useCallback((e: React.DragEvent<HTMLDivElement>) => {
    e.preventDefault();
    e.dataTransfer.dropEffect = "move";
  }, []);

  const onDrop = useCallback(
    (e: React.DragEvent<HTMLDivElement>) => {
      e.preventDefault();
      const type = e.dataTransfer.getData("application/agentoe-node") as NodeType | "";
      if (!type) return;
      const rect = (e.target as HTMLElement)
        .closest(".react-flow")
        ?.getBoundingClientRect();
      const pos: XYPosition = rect
        ? { x: e.clientX - rect.left, y: e.clientY - rect.top }
        : { x: 240, y: 160 };
      addNodeFromPalette(type, pos);
    },
    [addNodeFromPalette],
  );

  // ── 최초 마운트: 시나리오 목록 로드 (실패는 조용히 무시) ──────────
  useEffect(() => {
    void refreshList();
    // refreshList 는 스토어 참조로 안정적이지만 의존성 lint 를 위해 제외
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const hasError = issues.some((i) => i.severity === "error");
  const authed = token.trim().length > 0;

  return (
    <div className="app">
      <header className="toolbar">
        <h1>AgentOE Scenario Builder</h1>
        <span className="meta">tenant</span>
        <input
          type="text"
          value={meta.tenant_id}
          style={{ width: 100 }}
          onChange={(e) => setMeta({ tenant_id: e.target.value })}
        />
        <span className="meta">scenario_id</span>
        <input
          type="text"
          value={meta.scenario_id}
          onChange={(e) => setMeta({ scenario_id: e.target.value })}
        />
        <span className="meta">name</span>
        <input
          type="text"
          value={meta.name}
          onChange={(e) => setMeta({ name: e.target.value })}
        />
        <span className="grow" />
        <button
          type="button"
          onClick={() => {
            void refreshList();
            setShowList(true);
          }}
          disabled={busy}
        >
          불러오기…
        </button>
        <span
          className="meta"
          title={authed ? "JWT 설정됨" : "X-Tenant-Id 헤더 모드 (dev)"}
          style={{
            color: authed ? "#059669" : "#b45309",
            fontSize: 11,
            fontWeight: 600,
          }}
        >
          {authed ? "🔒 JWT" : "⚠ dev"}
        </span>
        <input
          type="password"
          placeholder="JWT (선택)"
          value={token}
          onChange={(e) => setToken(e.target.value)}
          style={{ width: 160 }}
          autoComplete="off"
        />
        <button type="button" onClick={() => void saveCurrent()} disabled={busy || !assembled.scenario}>
          {busy ? "저장 중…" : "Save"}
        </button>
        <button
          type="button"
          className="primary"
          onClick={() => void publishCurrent()}
          disabled={busy || !assembled.scenario || hasError}
        >
          {busy ? "…" : "Publish"}
        </button>
      </header>

      <Palette />

      <main className="canvas" onDrop={onDrop} onDragOver={onDragOver}>
        <ReactFlow
          nodes={highlightedNodes}
          edges={edges}
          onNodesChange={onNodesChange}
          onEdgesChange={onEdgesChange}
          onConnect={onConnect}
          onSelectionChange={onSelectionChange}
          nodeTypes={NODE_TYPES}
          fitView
        >
          <Background />
          <Controls />
          <MiniMap />
        </ReactFlow>
      </main>

      <aside className="sidebar">
        {error ? (
          <div className="section">
            <div
              className="issue error"
              style={{ padding: 8, borderRadius: 4, cursor: "pointer" }}
              onClick={clearError}
              title="클릭해서 닫기"
            >
              {error}
            </div>
          </div>
        ) : null}
        <PropertyPanel
          node={selectedDsl}
          onChange={updateSelectedNode}
          isEntry={!!selectedId && meta.entry === selectedId}
          isFallback={!!selectedId && meta.fallback_node === selectedId}
          onSetEntry={setEntryToSelected}
          onSetFallback={toggleFallbackToSelected}
        />
        <ValidationPanel issues={issues} />
        <div className="section">
          <h2>DSL 미리보기</h2>
          <pre>
            {assembled.scenario
              ? JSON.stringify(assembled.scenario, null, 2)
              : `// invalid: ${assembled.error ?? ""}`}
          </pre>
        </div>
      </aside>

      <ToastStack />

      {showList ? (
        <div
          style={{
            position: "fixed",
            inset: 0,
            background: "rgba(15,23,42,0.55)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            zIndex: 50,
          }}
          onClick={() => setShowList(false)}
        >
          <div
            style={{
              background: "#fff",
              borderRadius: 8,
              padding: 20,
              minWidth: 420,
              maxHeight: "70vh",
              overflow: "auto",
              boxShadow: "0 20px 40px rgba(0,0,0,0.15)",
            }}
            onClick={(e) => e.stopPropagation()}
          >
            <h2 style={{ margin: "0 0 12px" }}>시나리오 불러오기</h2>
            {list.length === 0 ? (
              <p style={{ color: "#64748b", margin: 0 }}>
                저장된 시나리오가 없습니다.
              </p>
            ) : (
              <ul style={{ listStyle: "none", padding: 0, margin: 0 }}>
                {list.map((s) => (
                  <li key={s.scenario_id} style={{ padding: "6px 0" }}>
                    <button
                      type="button"
                      style={{
                        width: "100%",
                        textAlign: "left",
                        padding: "8px 10px",
                        border: "1px solid #e2e8f0",
                        borderRadius: 6,
                        background: "#f8fafc",
                        cursor: "pointer",
                      }}
                      onClick={async () => {
                        await loadScenario(s.scenario_id, "latest");
                        setShowList(false);
                      }}
                    >
                      <strong>{s.name}</strong>{" "}
                      <span style={{ color: "#64748b", fontSize: 12 }}>
                        v{s.version}
                        {s.published ? " · published" : ""}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
            <div style={{ textAlign: "right", marginTop: 16 }}>
              <button type="button" onClick={() => setShowList(false)}>
                닫기
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
