/**
 * 환경별 설정 관리 (dev / staging / prod)
 * — 3열 나란히 비교 뷰 + 차이 하이라이트
 * — 인라인 편집 + 저장
 * — N5.1: 실 backend 연동 (getConfig / getDiff / updateConfig)
 */
import { useEffect, useState } from "react";
import { useAuth } from "../providers/AuthProvider";
import {
  getConfig, getDiff, updateConfig,
  type EnvConfig, type ConfigDiff, type Env,
} from "../lib/api";

const ENVS: Env[] = ["dev", "staging", "prod"];
const ENV_LABEL: Record<Env, string> = { dev: "개발", staging: "검수", prod: "상용" };

function valStr(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "boolean") return v ? "true" : "false";
  return String(v);
}

function EnvBadge({ env }: { env: Env }) {
  return <span className={`env-badge ${env}`}><span className="header-dot" />{ENV_LABEL[env]}</span>;
}

export default function Config() {
  const { roles, username } = useAuth();
  const canEdit = (env: Env) => {
    if (env === "prod") return roles.includes("portal:admin");
    return roles.includes("portal:operator") || roles.includes("portal:admin");
  };

  const [configs, setConfigs] = useState<Record<Env, EnvConfig | null>>({ dev: null, staging: null, prod: null });
  const [diffs, setDiffs] = useState<ConfigDiff[]>([]);
  const [showDiffOnly, setShowDiffOnly] = useState(false);
  const [saving, setSaving] = useState<Env | null>(null);
  const [saveErr, setSaveErr] = useState<Env | null>(null);
  const [editValues, setEditValues] = useState<Record<Env, Record<string, string>>>({ dev: {}, staging: {}, prod: {} });

  const reload = async () => {
    const [d, s, p, df] = await Promise.all([
      getConfig("dev"), getConfig("staging"), getConfig("prod"), getDiff(),
    ]);
    setConfigs({ dev: d, staging: s, prod: p });
    setDiffs(df.diffs);
    setEditValues({
      dev:     Object.fromEntries(Object.entries(d.values).map(([k, v]) => [k, valStr(v)])),
      staging: Object.fromEntries(Object.entries(s.values).map(([k, v]) => [k, valStr(v)])),
      prod:    Object.fromEntries(Object.entries(p.values).map(([k, v]) => [k, valStr(v)])),
    });
  };

  useEffect(() => { void reload(); }, []);

  const diffKeys = new Set(diffs.map((d) => d.key));

  const allKeys = configs.dev
    ? (showDiffOnly ? diffs.map((d) => d.key) : Object.keys(configs.dev.values))
    : [];

  const save = async (env: Env) => {
    if (!canEdit(env)) return;
    setSaving(env);
    setSaveErr(null);
    try {
      const updated = await updateConfig(env, {
        updated_by: username ?? "portal-user",
        values: editValues[env],
      });
      // 저장 후 configs 만 갱신 (editValues 는 그대로 유지)
      setConfigs((prev) => ({ ...prev, [env]: updated }));
      // diff 재계산을 위해 전체 reload
      void reload();
    } catch {
      setSaveErr(env);
    } finally {
      setSaving(null);
    }
  };

  if (!configs.dev) return <div className="spinner" />;

  return (
    <div>
      <div className="page-header">
        <div>
          <div className="page-title">환경별 설정 관리</div>
          <div className="page-sub">
            {diffs.length}개 키에서 환경 간 차이 발생
          </div>
        </div>
        <div className="flex items-center gap-8">
          <label className="flex items-center gap-8" style={{ fontSize: 13, color: "var(--text-2)", cursor: "pointer" }}>
            <input
              type="checkbox"
              checked={showDiffOnly}
              onChange={(e) => setShowDiffOnly(e.target.checked)}
              style={{ accentColor: "var(--blue)" }}
            />
            차이만 보기
          </label>
        </div>
      </div>

      {/* 3-env 저장 버튼 행 */}
      <div style={{ display: "grid", gridTemplateColumns: "220px 1fr 1fr 1fr", gap: 0, marginBottom: 14 }}>
        <div />
        {ENVS.map((env) => (
          <div key={env} style={{ padding: "0 8px" }}>
            <div className="flex items-center gap-8" style={{ marginBottom: 8 }}>
              <EnvBadge env={env} />
              <span style={{ fontSize: 11, color: "var(--text-3)" }}>
                {configs[env]?.updated_by} · {configs[env]?.updated_at.slice(0, 10)}
              </span>
            </div>
            <button
              className="btn btn-ghost btn-sm full-w"
              disabled={saving === env || !canEdit(env)}
              title={!canEdit(env) ? (env === "prod" ? "prod 설정은 portal:admin 전용" : "portal:operator 이상 필요") : undefined}
              onClick={() => void save(env)}
            >
              {saving === env
                ? "저장 중..."
                : saveErr === env
                  ? "❌ 저장 실패"
                  : "💾 저장"}
            </button>
          </div>
        ))}
      </div>

      {/* 설정 그리드 */}
      <div className="card" style={{ padding: 0, overflow: "hidden" }}>
        <div className="config-grid">
          {/* 헤더 */}
          <div className="config-hdr">설정 키</div>
          {ENVS.map((env) => <div key={env} className="config-hdr" style={{ textAlign: "center" }}>{ENV_LABEL[env]}</div>)}

          {/* 행 */}
          {allKeys.map((key) => {
            const isDiff = diffKeys.has(key);
            return (
              <>
                <div key={`${key}-k`} className={`config-cell key ${isDiff ? "diff" : ""}`}>
                  {isDiff && <span style={{ color: "var(--yellow)", marginRight: 5 }}>⚑</span>}
                  {key}
                </div>
                {ENVS.map((env) => (
                  <div key={`${key}-${env}`} className={`config-cell ${isDiff ? "diff" : ""}`}>
                    <input
                      className="config-val-input"
                      value={editValues[env][key] ?? ""}
                      onChange={(e) =>
                        setEditValues((prev) => ({
                          ...prev,
                          [env]: { ...prev[env], [key]: e.target.value },
                        }))
                      }
                    />
                  </div>
                ))}
              </>
            );
          })}
        </div>
      </div>
    </div>
  );
}
