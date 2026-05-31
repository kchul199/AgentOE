/**
 * 좌측 노드 팔레트 — drag source.
 *
 * 개선 (Dark-Pro 리디자인):
 *   - 노드를 4개 그룹(흐름 / AI / 제어 / 통합)으로 섹션 구분
 *   - 타입 아이콘 + 색상 배경 아이콘 박스
 *   - 다크 테마 스타일 적용
 */
import { NODE_PALETTE, type NodeType } from "@/types/scenario";

// ── 타입별 아이콘 ─────────────────────────────────────────────────────────
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

// ── 그룹 소속 ─────────────────────────────────────────────────────────────
const NODE_GROUP: Record<NodeType, string> = {
  start:    "흐름",
  end:      "흐름",
  intent:   "AI",
  llm:      "AI",
  branch:   "제어",
  wait:     "제어",
  tool:     "통합",
  transfer: "통합",
  context:  "통합",
};

const GROUP_ORDER = ["흐름", "AI", "제어", "통합"];

export default function Palette(): JSX.Element {
  const onDragStart = (e: React.DragEvent<HTMLDivElement>, type: string) => {
    e.dataTransfer.setData("application/agentoe-node", type);
    e.dataTransfer.effectAllowed = "move";
  };

  // NODE_PALETTE 순서를 유지하면서 그룹별 분류
  const grouped = GROUP_ORDER.map((group) => ({
    group,
    items: NODE_PALETTE.filter((p) => NODE_GROUP[p.type] === group),
  }));

  return (
    <aside className="palette">
      {grouped.map(({ group, items }) => (
        <div key={group}>
          <div className="palette-section-label">{group}</div>
          {items.map((p) => (
            <div
              key={p.type}
              className="palette-item"
              draggable
              onDragStart={(e) => onDragStart(e, p.type)}
              title={`${p.label} — ${p.description}\n드래그해서 캔버스에 추가`}
            >
              {/* 아이콘 박스 */}
              <div
                className="pi-icon"
                style={{
                  background: `${p.color}22`,
                  color: p.color,
                }}
              >
                {NODE_ICONS[p.type]}
              </div>

              {/* 이름 + 설명 */}
              <div className="pi-text">
                <div className="pi-name">{p.label}</div>
                <div className="pi-desc">{p.description}</div>
              </div>
            </div>
          ))}
        </div>
      ))}
    </aside>
  );
}
