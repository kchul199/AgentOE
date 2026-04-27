/**
 * 좌측 노드 팔레트 — drag source. Canvas 의 onDrop 으로 타입을 전달한다.
 *
 * 데이터 채널: dataTransfer.setData("application/agentoe-node", type)
 */
import { NODE_PALETTE } from "@/types/scenario";

export default function Palette(): JSX.Element {
  const onDragStart = (e: React.DragEvent<HTMLDivElement>, type: string) => {
    e.dataTransfer.setData("application/agentoe-node", type);
    e.dataTransfer.effectAllowed = "move";
  };

  return (
    <aside className="palette">
      <h2>노드 팔레트</h2>
      {NODE_PALETTE.map((p) => (
        <div
          key={p.type}
          className="palette-item"
          draggable
          onDragStart={(e) => onDragStart(e, p.type)}
        >
          <div className="pi-head">
            <span className="pi-dot" style={{ background: p.color }} />
            <span>{p.label}</span>
          </div>
          <div className="pi-desc">{p.description}</div>
        </div>
      ))}
    </aside>
  );
}
