/**
 * Scenario Builder 루트 — 툴바 + 팔레트 + React Flow 캔버스 + 탭 사이드바 + 상태바.
 *
 * 개선 (Dark-Pro 리디자인):
 *   - 다크 툴바: 로고 · 프로젝트 메타 · 인증 · 액션 그룹 분리
 *   - 사이드바 탭: 속성 / 검증 (오류 배지) / DSL 미리보기
 *   - 하단 상태바: 노드 수 · 엣지 수 · 오류/정상 상태
 *   - 모달: 블러 오버레이 + 슬라이드 애니메이션
 */
import { useCallback, useEffect, useMemo, useState } from "react";
import ReactFlow, {
  Background,
  BackgroundVariant,
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
  // ── 스토어 구독 ──────────────────────────────────────────────────────
  const edges      = useBuilderStore((s) => s.edges);
  const meta       = useBuilderStore((s) => s.meta);
  const selectedId = useBuilderStore((s) => s.selectedId);
  const busy       = useBuilderStore((s) => s.busy);
  const error      = useBuilderStore((s) => s.error);
  const list       = useBuilderStore((s) => s.list);
  const token      = useBuilderStore((s) => s.token);
  const nodes      = useBuilderStore((s) => s.nodes);

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
  const onNodesChange          = useBuilderStore((s) => s.onNodesChange);
  const onEdgesChange          = useBuilderStore((s) => s.onEdgesChange);
  const onConnect              = useBuilderStore((s) => s.onConnect);
  const setSelectedId          = useBuilderStore((s) => s.setSelectedId);
  const addNodeFromPalette     = useBuilderStore((s) => s.addNodeFromPalette);
  const updateSelectedNode     = useBuilderStore((s) => s.updateSelectedNode);
  const setEntryToSelected     = useBuilderStore((s) => s.setEntryToSelected);
  const toggleFallbackToSelected = useBuilderStore((s) => s.toggleFallbackToSelected);
  const setMeta                = useBuilderStore((s) => s.setMeta);
  const setToken               = useBuilderStore((s) => s.setToken);
  const clearError             = useBuilderStore((s) => s.clearError);
  const refreshList            = useBuilderStore((s) => s.refreshList);
  const loadScenario           = useBuilderStore((s) => s.loadScenario);
  const saveCurrent            = useBuilderStore((s) => s.saveCurrent);
  const publishCurrent         = useBuilderStore((s) => s.publishCurrent);

  // ── 로컬 UI 상태 ────────────────────────────────────────────────────
  const [showList, setShowList]   = useState(false);
  const [activeTab, setActiveTab] = useState<"props" | "validation" | "dsl">("props");

  // ── 파생 수치 ────────────────────────────────────────────────────────
  const errorCount = issues.filter((i) => i.severity === "error").length;
  const warnCount  = issues.filter((i) => i.severity === "warning").length;
  const hasError   = errorCount > 0;
  const authed     = token.trim().length > 0;

  // 오류 발생 시 자동으로 검증 탭 강조 (탭 자동 전환은 하지 않음 — UX 혼란 방지)

  // ── React Flow 이벤트 ────────────────────────────────────────────────
  const onSelectionChange = useCallback(
    (p: OnSelectionChangeParams) => setSelectedId(p.nodes[0]?.id ?? null),
    [setSelectedId],
  );

  // ── Drag & Drop ──────────────────────────────────────────────────────
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

  // ── 최초 마운트: 시나리오 목록 로드 ─────────────────────────────────
  useEffect(() => {
    void refreshList();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="app">
      {/* ── Toolbar ───────────────────────────────────────────────── */}
      <header className="toolbar">
        {/* 로고 */}
        <div className="toolbar-brand">
          <div className="toolbar-logo">⚡</div>
          <span className="toolbar-title">AgentOE</span>
        </div>

        <div className="toolbar-divider" />

        {/* 프로젝트 메타 */}
        <div className="toolbar-meta">
          <div className="toolbar-meta-field">
            <span className="toolbar-meta-label">tenant</span>
            <input
              type="text"
              value={meta.tenant_id}
              style={{ width: 88 }}
              onChange={(e) => setMeta({ tenant_id: e.target.value })}
            />
          </div>
          <span className="toolbar-meta-sep">/</span>
          <div className="toolbar-meta-field">
            <span className="toolbar-meta-label">scenario_id</span>
            <input
              type="text"
              value={meta.scenario_id}
              style={{ width: 138 }}
              onChange={(e) => setMeta({ scenario_id: e.target.value })}
            />
          </div>
          <span className="toolbar-meta-sep">/</span>
          <div className="toolbar-meta-field">
            <span className="toolbar-meta-label">name</span>
            <input
              type="text"
              value={meta.name}
              style={{ width: 138 }}
              onChange={(e) => setMeta({ name: e.target.value })}
            />
          </div>
        </div>

        <div className="grow" />

        {/* 인증 + 액션 */}
        <div className="toolbar-right">
          <span className={`auth-badge ${authed ? "authed" : "dev"}`}>
            {authed ? "🔒 JWT" : "⚠ dev"}
          </span>
          <input
            className="token-input"
            type="password"
            placeholder="JWT token (선택)"
            value={token}
            onChange={(e) => setToken(e.target.value)}
            autoComplete="off"
          />
          <button
            className="btn"
            type="button"
            onClick={() => { void refreshList(); setShowList(true); }}
            disabled={busy}
          >
            불러오기
          </button>
          <button
            className="btn"
            type="button"
            onClick={() => void saveCurrent()}
            disabled={busy || !assembled.scenario}
          >
            {busy ? "저장 중…" : "저장"}
          </button>
          <button
            className="btn btn-primary"
            type="button"
            onClick={() => void publishCurrent()}
            disabled={busy || !assembled.scenario || hasError}
          >
            {busy ? "…" : "▲ Publish"}
          </button>
        </div>
      </header>

      {/* ── Palette ───────────────────────────────────────────────── */}
      <Palette />

      {/* ── Canvas ────────────────────────────────────────────────── */}
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
          <Background
            variant={BackgroundVariant.Dots}
            color="#c8d4e0"
            gap={18}
            size={1}
          />
          <Controls />
          <MiniMap
            style={{ background: "#1e2a3f" }}
            maskColor="rgba(15,22,35,0.4)"
          />
        </ReactFlow>
      </main>

      {/* ── Sidebar ───────────────────────────────────────────────── */}
      <aside className="sidebar">
        {/* 탭 바 */}
        <div className="sidebar-tabs">
          <button
            className={`sidebar-tab ${activeTab === "props" ? "active" : ""}`}
            onClick={() => setActiveTab("props")}
          >
            속성
          </button>
          <button
            className={`sidebar-tab ${activeTab === "validation" ? "active" : ""}`}
            onClick={() => setActiveTab("validation")}
          >
            검증
            {errorCount > 0 && (
              <span className="tab-badge error">{errorCount}</span>
            )}
            {errorCount === 0 && warnCount > 0 && (
              <span className="tab-badge warn">{warnCount}</span>
            )}
          </button>
          <button
            className={`sidebar-tab ${activeTab === "dsl" ? "active" : ""}`}
            onClick={() => setActiveTab("dsl")}
          >
            DSL
          </button>
        </div>

        {/* 탭 콘텐츠 */}
        <div className="sidebar-content">
          {/* 오류 배너 (어느 탭에서든 표시) */}
          {error ? (
            <div
              className="error-banner"
              onClick={clearError}
              title="클릭해서 닫기"
            >
              <span>✕</span>
              <span>{error}</span>
            </div>
          ) : null}

          {activeTab === "props" && (
            <PropertyPanel
              node={selectedDsl}
              onChange={updateSelectedNode}
              isEntry={!!selectedId && meta.entry === selectedId}
              isFallback={!!selectedId && meta.fallback_node === selectedId}
              onSetEntry={setEntryToSelected}
              onSetFallback={toggleFallbackToSelected}
            />
          )}

          {activeTab === "validation" && <ValidationPanel issues={issues} />}

          {activeTab === "dsl" && (
            <div className="dsl-wrap">
              <pre className="dsl-pre">
                {assembled.scenario
                  ? JSON.stringify(assembled.scenario, null, 2)
                  : `// invalid\n// ${assembled.error ?? ""}`}
              </pre>
            </div>
          )}
        </div>
      </aside>

      {/* ── Status Bar ────────────────────────────────────────────── */}
      <div className="statusbar">
        <div className="sb-item">
          <span className="sb-dot" style={{ background: "#6366f1" }} />
          {nodes.length} nodes
        </div>
        <div className="sb-sep" />
        <div className="sb-item">{edges.length} edges</div>
        <div className="sb-sep" />
        {hasError ? (
          <div className="sb-item sb-error">
            ✕ {errorCount} error{errorCount > 1 ? "s" : ""}
          </div>
        ) : (
          <div className="sb-item sb-ok">✓ valid</div>
        )}
        {warnCount > 0 && !hasError && (
          <>
            <div className="sb-sep" />
            <div className="sb-item sb-warn">⚠ {warnCount} warning{warnCount > 1 ? "s" : ""}</div>
          </>
        )}
        <div className="sb-grow" />
        {meta.scenario_id && (
          <div className="sb-item sb-id">
            {meta.tenant_id} / {meta.scenario_id}
            {meta.name && meta.name !== meta.scenario_id ? ` — ${meta.name}` : ""}
          </div>
        )}
      </div>

      {/* ── Toast ─────────────────────────────────────────────────── */}
      <ToastStack />

      {/* ── Load Modal ────────────────────────────────────────────── */}
      {showList ? (
        <div
          className="modal-overlay"
          onClick={() => setShowList(false)}
        >
          <div className="modal" onClick={(e) => e.stopPropagation()}>
            <div className="modal-header">
              <h2>시나리오 불러오기</h2>
              <button
                className="modal-close"
                type="button"
                onClick={() => setShowList(false)}
              >
                ✕
              </button>
            </div>
            <div className="modal-body">
              {list.length === 0 ? (
                <div className="modal-empty">저장된 시나리오가 없습니다.</div>
              ) : (
                <div className="scenario-list">
                  {list.map((s) => (
                    <button
                      key={s.scenario_id}
                      className="scenario-list-item"
                      type="button"
                      onClick={async () => {
                        await loadScenario(s.scenario_id, "latest");
                        setShowList(false);
                      }}
                    >
                      <div className="sli-name">{s.name}</div>
                      <div className="sli-meta">
                        <span>{s.scenario_id}</span>
                        <span>v{s.version}</span>
                        {s.published ? (
                          <span className="sli-published">published</span>
                        ) : null}
                      </div>
                    </button>
                  ))}
                </div>
              )}
            </div>
          </div>
        </div>
      ) : null}
    </div>
  );
}
