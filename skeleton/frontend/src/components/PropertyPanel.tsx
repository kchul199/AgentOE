/**
 * 우측 속성 패널 — 선택된 노드의 config 를 편집한다.
 *
 * 편집은 data.dsl 을 얕은 복사로 갱신해 상위 (App.tsx) 로 전달.
 * 각 노드 타입별로 별도의 폼을 제공한다. LLM/Tool/Intent 가 자주 쓰이므로
 * 이들은 완전한 필드 세트, 나머지는 핵심 필드만.
 */
import type { ScenarioNode } from "@/types/scenario";

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
      <div className="section">
        <h2>노드 속성</h2>
        <div style={{ fontSize: 13, color: "var(--fg-muted)" }}>
          캔버스에서 노드를 선택하세요.
        </div>
      </div>
    );
  }

  const update = (patch: Partial<ScenarioNode>) => {
    onChange({ ...node, ...patch } as ScenarioNode);
  };
  const updateConfig = (patch: Record<string, unknown>) => {
    onChange({
      ...node,
      config: { ...node.config, ...patch },
    } as ScenarioNode);
  };

  return (
    <div className="section">
      <h2>노드 속성 · {node.type}</h2>

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

      <div style={{ marginTop: 10, display: "flex", gap: 6 }}>
        <button
          type="button"
          onClick={onSetEntry}
          disabled={isEntry}
          style={{
            flex: 1,
            padding: "4px 8px",
            fontSize: 12,
            borderRadius: 4,
            border: "1px solid var(--border)",
            background: isEntry ? "#dcfce7" : "var(--surface)",
            cursor: isEntry ? "default" : "pointer",
          }}
        >
          {isEntry ? "✓ Entry" : "Entry 로 지정"}
        </button>
        <button
          type="button"
          onClick={onSetFallback}
          style={{
            flex: 1,
            padding: "4px 8px",
            fontSize: 12,
            borderRadius: 4,
            border: "1px solid var(--border)",
            background: isFallback ? "#fef3c7" : "var(--surface)",
            cursor: "pointer",
          }}
        >
          {isFallback ? "✓ Fallback" : "Fallback 으로 지정"}
        </button>
      </div>

      <div style={{ height: 12 }} />
      <h2>Config</h2>
      <ConfigEditor node={node} onChange={updateConfig} />
    </div>
  );
}

function ConfigEditor({
  node,
  onChange,
}: {
  node: ScenarioNode;
  onChange: (patch: Record<string, unknown>) => void;
}): JSX.Element {
  switch (node.type) {
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
                onChange={(e) => onChange({ temperature: Number(e.target.value) })}
              />
            </div>
            <div>
              <label>max_tokens</label>
              <input
                type="number"
                value={node.config.max_tokens}
                onChange={(e) => onChange({ max_tokens: Number(e.target.value) })}
              />
            </div>
          </div>
          <label>
            <input
              type="checkbox"
              checked={node.config.streaming}
              onChange={(e) => onChange({ streaming: e.target.checked })}
            />{" "}
            streaming
          </label>
          <label>
            <input
              type="checkbox"
              checked={node.config.enable_filler}
              onChange={(e) => onChange({ enable_filler: e.target.checked })}
            />{" "}
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
            />{" "}
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
                /* ignore — 사용자가 타이핑 중 */
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
