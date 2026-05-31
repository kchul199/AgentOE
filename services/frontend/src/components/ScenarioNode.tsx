/**
 * React Flow 커스텀 노드 — 시나리오 노드 시각 컴포넌트.
 *
 * 개선 (Dark-Pro 리디자인):
 *   - 상단 컬러 스트라이프 (노드 타입별 색상)
 *   - 타입 아이콘 + 타입 pill
 *   - config 핵심 요약 1줄 (sn-summary)
 *   - entry / fallback / selected 상태별 ring 강조
 */
import { Handle, Position, type NodeProps } from "reactflow";

import { paletteColor } from "@/lib/dsl";
import type { NodeType, ScenarioNode as DslNode } from "@/types/scenario";

// ── 타입별 아이콘 (유니코드 기호) ────────────────────────────────────────
const NODE_ICONS: Record<NodeType, string> = {
  start:    "▶",
  intent:   "◈",
  llm:      "◉",
  tool:     "⚙",
  branch:   "⑂",
  transfer: "⇝",
  wait:     "◷",
  context:  "≡",
  end:      "■",
};

// ── Config 핵심 요약 (노드 카드 하단 1줄) ────────────────────────────────
function configSummary(n: DslNode): string | null {
  switch (n.type) {
    case "start":
      return n.config.trigger_type.replace(/_/g, " ");
    case "intent":
      return n.config.labels.slice(0, 3).join(" · ");
    case "llm":
      return n.config.model;
    case "tool":
      return n.config.tool_name || "(tool_name 미설정)";
    case "branch":
      return `mode: ${n.config.mode}`;
    case "transfer":
      return `→ ${n.config.queue}`;
    case "wait":
      return `⏱ ${n.config.timeout_s}s`;
    case "context": {
      const setCount = Object.keys(n.config.set_slots).length;
      const clearCount = n.config.clear_slots.length;
      return `set ${setCount} · clear ${clearCount}`;
    }
    case "end":
      if (!n.config.closing_message) return null;
      return n.config.closing_message.length > 28
        ? n.config.closing_message.slice(0, 28) + "…"
        : n.config.closing_message;
    default:
      return null;
  }
}

export interface ScenarioNodeData {
  dsl: DslNode;
  highlight?: "entry" | "fallback" | null;
}

export default function ScenarioNode({
  data,
  selected,
}: NodeProps<ScenarioNodeData>): JSX.Element {
  const n = data.dsl;
  const color = paletteColor(n.type);
  const icon = NODE_ICONS[n.type];
  const summary = configSummary(n);

  const cls = [
    "sn",
    selected ? "sn--selected" : "",
    data.highlight === "entry" ? "sn--entry" : "",
    data.highlight === "fallback" ? "sn--fallback" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={cls}
      style={{ "--node-color": color } as React.CSSProperties}
    >
      <Handle type="target" position={Position.Left} />

      {/* 상단 컬러 스트라이프 */}
      <div className="sn-stripe" />

      <div className="sn-body">
        {/* 헤더: 아이콘 + 타입 배지 */}
        <div className="sn-header">
          <div
            className="sn-icon"
            style={{ background: `${color}22`, color }}
          >
            {icon}
          </div>
          <span className="sn-type" style={{ background: color }}>
            {n.type}
          </span>
        </div>

        {/* ID (굵게) */}
        <div className="sn-id">{n.id}</div>

        {/* 선택적: 라벨 */}
        {n.label ? <div className="sn-label">{n.label}</div> : null}

        {/* 선택적: config 요약 */}
        {summary ? <div className="sn-summary">{summary}</div> : null}
      </div>

      <Handle type="source" position={Position.Right} />
    </div>
  );
}
