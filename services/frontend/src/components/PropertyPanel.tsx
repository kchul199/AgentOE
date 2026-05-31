/**
 * 우측 속성 패널 — 선택된 노드의 config 를 편집한다.
 *
 * 개선 (Dark-Pro 리디자인):
 *   - 인라인 style 제거 → CSS 클래스 기반
 *   - Entry / Fallback 버튼 → .node-meta-btn
 *   - checkbox → 토글 스위치 (CSS appearance:none)
 *   - 빈 상태 empty 뷰
 */
import { paletteColor } from "@/lib/dsl";
import type { NodeType, ScenarioNode } from "@/types/scenario";

const NODE_ICONS: Record<NodeType, string> = {
  start: "▶", intent: "◈", llm: "◉", tool: "⚙",
  branch: "⑂", transfer: "⇝", wait: "◷", context: "≡", end: "■",
};

interface Props {
  node: ScenarioNode | null;
  onChange: (next: ScenarioNode) => void;
  isEntry: boolean;
  isFallback: boolean;
  onSetEntry: () => void;
  onSetFallback: () => void;
}

export default function PropertyPanel({
  node,
  onChange,
  isEntry,
  isFallback,
  onSetEntry,
  onSetFallback,
}: Props): JSX.Element {
  if (!node) {
    return (
      <div className="section props-empty">
        <div className="props-empty-icon">◻</div>
        <div className="props-empty-text">
          캔버스에서 노드를 클릭하면<br />여기에 속성이 표시됩니다.
        </div>
      </div>
    );
  }

  const color = paletteColor(node.type);

  const update = (patch: Partial<ScenarioNode>) =>
    onChange({ ...node, ...patch } as ScenarioNode);

  const updateConfig = (patch: Record<string, unknown>) =>
    onChange({ ...node, config: { ...node.config, ...patch } } as ScenarioNode);

  return (
    <div className="section">
      {/* 노드 타입 헤더 */}
      <div className="props-node-header">
        <span style={{ fontSize: 14, color }}>{NODE_ICONS[node.type]}</span>
        <span className="props-type-badge" style={{ background: color }}>
          {node.type}
        </span>
      </div>

      <label>ID</label>
      <input
        type="text"
        value={node.id}
        onChange={(e) => update({ id: e.target.value })}
      />

      <label>Label (GUI 표시)</label>
      <input
        type="text"
        value={node.label ?? ""}
        onChange={(e) => update({ label: e.target.value || undefined })}
      />

      <label>Description</label>
      <textarea
        value={node.description ?? ""}
        onChange={(e) => update({ description: e.target.value || undefined })}
      />

      <div className="node-meta-buttons">
        <button
          type="button"
          className={`node-meta-btn ${isEntry ? "active-entry" : ""}`}
          onClick={onSetEntry}
          disabled={isEntry}
        >
          {isEntry ? "✓ Entry" : "Entry 지정"}
        </button>
        <button
          type="button"
          className={`node-meta-btn ${isFallback ? "active-fallback" : ""}`}
          onClick={onSetFallback}
        >
          {isFallback ? "✓ Fallback" : "Fallback 지정"}
        </button>
      </div>

      <div className="config-section-divider" />
      <h2>Config</h2>
      <ConfigEditor node={node} onChange={updateConfig} />
    </div>
  );
}

// ── Config 편집기 (노드 타입별) ──────────────────────────────────────────────

function ConfigEditor({
  node,
  onChange,
}: {
  node: ScenarioNode;
  onChange: (patch: Record<string, unknown>) => void;
}): JSX.Element {
  switch (node.type) {
    case "start":
      return (
        <>
          <label>trigger_type</label>
          <select
            value={node.config.trigger_type}
            onChange={(e) => onChange({ trigger_type: e.target.value })}
          >
            <option value="inbound_call">inbound_call — 수신 전화</option>
            <option value="outbound_call">outbound_call — 발신 전화</option>
            <option value="scheduled">scheduled — 예약 발신</option>
          </select>
          <label>greeting_message (선택)</label>
          <textarea
            placeholder="통화 연결 직후 인사 멘트 (비우면 무음 시작)"
            value={node.config.greeting_message ?? ""}
            onChange={(e) =>
              onChange({ greeting_message: e.target.value || null })
            }
          />
          <div className="start-info-box">
            ✓ Start 노드는 시나리오에 1개만 허용됩니다.<br />
            이 노드가 자동으로 Entry 로 지정됩니다.
          </div>
        </>
      );

    case "intent":
      return (
        <>
          <label>labels (쉼표 구분)</label>
          <input
            type="text"
            value={node.config.labels.join(",")}
            onChange={(e) =>
              onChange({
                labels: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
          />
          <label>model</label>
          <input
            type="text"
            value={node.config.model}
            onChange={(e) => onChange({ model: e.target.value })}
          />
          <label>threshold</label>
          <input
            type="number"
            step="0.05"
            value={node.config.threshold}
            onChange={(e) => onChange({ threshold: Number(e.target.value) })}
          />
        </>
      );

    case "llm":
      return (
        <>
          <label>system_prompt</label>
          <textarea
            value={node.config.system_prompt}
            onChange={(e) => onChange({ system_prompt: e.target.value })}
          />
          <label>model</label>
          <input
            type="text"
            value={node.config.model}
            onChange={(e) => onChange({ model: e.target.value })}
          />
          <label>fallback_model</label>
          <input
            type="text"
            value={node.config.fallback_model}
            onChange={(e) => onChange({ fallback_model: e.target.value })}
          />
          <div className="row">
            <div>
              <label>temperature</label>
              <input
                type="number"
                step="0.1"
                value={node.config.temperature}
                onChange={(e) =>
                  onChange({ temperature: Number(e.target.value) })
                }
              />
            </div>
            <div>
              <label>max_tokens</label>
              <input
                type="number"
                value={node.config.max_tokens}
                onChange={(e) =>
                  onChange({ max_tokens: Number(e.target.value) })
                }
              />
            </div>
          </div>
          <label>
            <input
              type="checkbox"
              checked={node.config.streaming}
              onChange={(e) => onChange({ streaming: e.target.checked })}
            />
            streaming
          </label>
          <label>
            <input
              type="checkbox"
              checked={node.config.enable_filler}
              onChange={(e) => onChange({ enable_filler: e.target.checked })}
            />
            enable_filler
          </label>
        </>
      );

    case "tool":
      return (
        <>
          <label>tool_name</label>
          <input
            type="text"
            value={node.config.tool_name}
            onChange={(e) => onChange({ tool_name: e.target.value })}
          />
          <label>timeout_s</label>
          <input
            type="number"
            step="0.5"
            value={node.config.timeout_s}
            onChange={(e) => onChange({ timeout_s: Number(e.target.value) })}
          />
          <label>retry</label>
          <input
            type="number"
            value={node.config.retry}
            onChange={(e) => onChange({ retry: Number(e.target.value) })}
          />
          <label>on_error</label>
          <select
            value={node.config.on_error}
            onChange={(e) => onChange({ on_error: e.target.value })}
          >
            <option value="fallback">fallback</option>
            <option value="raise">raise</option>
            <option value="continue">continue</option>
          </select>
        </>
      );

    case "branch":
      return (
        <>
          <label>mode</label>
          <select
            value={node.config.mode}
            onChange={(e) => onChange({ mode: e.target.value })}
          >
            <option value="intent">intent</option>
            <option value="expr">expr</option>
            <option value="slot">slot</option>
          </select>
          {node.config.mode === "slot" ? (
            <>
              <label>slot_key</label>
              <input
                type="text"
                value={node.config.slot_key ?? ""}
                onChange={(e) => onChange({ slot_key: e.target.value })}
              />
            </>
          ) : null}
        </>
      );

    case "transfer":
      return (
        <>
          <label>queue</label>
          <input
            type="text"
            value={node.config.queue}
            onChange={(e) => onChange({ queue: e.target.value })}
          />
          <label>reason</label>
          <input
            type="text"
            value={node.config.reason}
            onChange={(e) => onChange({ reason: e.target.value })}
          />
          <label>
            <input
              type="checkbox"
              checked={node.config.include_summary}
              onChange={(e) => onChange({ include_summary: e.target.checked })}
            />
            include_summary
          </label>
        </>
      );

    case "wait":
      return (
        <>
          <label>timeout_s</label>
          <input
            type="number"
            step="0.5"
            value={node.config.timeout_s}
            onChange={(e) => onChange({ timeout_s: Number(e.target.value) })}
          />
          <label>prompt_on_timeout</label>
          <textarea
            value={node.config.prompt_on_timeout ?? ""}
            onChange={(e) =>
              onChange({ prompt_on_timeout: e.target.value || null })
            }
          />
        </>
      );

    case "context":
      return (
        <>
          <label>set_slots (JSON)</label>
          <textarea
            value={JSON.stringify(node.config.set_slots, null, 2)}
            onChange={(e) => {
              try {
                onChange({ set_slots: JSON.parse(e.target.value) });
              } catch {
                /* 타이핑 중 무시 */
              }
            }}
          />
          <label>clear_slots (쉼표 구분)</label>
          <input
            type="text"
            value={node.config.clear_slots.join(",")}
            onChange={(e) =>
              onChange({
                clear_slots: e.target.value
                  .split(",")
                  .map((s) => s.trim())
                  .filter(Boolean),
              })
            }
          />
        </>
      );

    case "end":
      return (
        <>
          <label>closing_message</label>
          <textarea
            value={node.config.closing_message ?? ""}
            onChange={(e) =>
              onChange({ closing_message: e.target.value || null })
            }
          />
        </>
      );

    default: {
      const _n: never = node;
      return <div>unreachable: {JSON.stringify(_n)}</div>;
    }
  }
}
