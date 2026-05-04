/**
 * React Flow 커스텀 노드 — 시나리오 노드 시각 컴포넌트.
 *
 * entry/fallback 강조는 App.tsx 가 클래스를 data.highlight 로 주입.
 */
import { Handle, Position, type NodeProps } from "reactflow";

import { paletteColor } from "@/lib/dsl";
import type { ScenarioNode as DslNode } from "@/types/scenario";

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
  const classes = [
    "scenario-node",
    selected ? "selected" : "",
    data.highlight === "entry" ? "entry" : "",
    data.highlight === "fallback" ? "fallback" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes}>
      <Handle type="target" position={Position.Left} />
      <div className="sn-head">
        <span className="sn-type" style={{ background: color }}>
          {n.type}
        </span>
        <span className="sn-id">{n.id}</span>
      </div>
      {n.label ? <div className="sn-label">{n.label}</div> : null}
      <Handle type="source" position={Position.Right} />
    </div>
  );
}
