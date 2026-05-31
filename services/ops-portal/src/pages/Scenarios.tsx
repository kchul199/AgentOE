/**
 * 시나리오 관리 (Phase N — N4.2)
 *
 * 변경 (N4.2):
 *   - 이름 검색 입력창 (debounce 400ms)
 *   - published / 초안 / 전체 필터 chip
 *   - tenant_id 필터 select (목록에서 추출)
 *   - 태그 클릭 → 태그 필터 진입
 *   - 페이지 하단 총 건수 표시
 *   - testScenario / deployScenario api.ts 함수 연동 (타입 정합성 수정)
 *   - getScenarios → list.items 구조 분해 (ScenarioListResponse.items)
 */
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getScenarios,
  testScenario,
  deployScenario,
  type Scenario,
  type Env,
} from "../lib/api";

// ── 상수 ──────────────────────────────────────────────────────────────────────
const ENVS: Env[] = ["dev", "staging", "prod"];
const ENV_LABEL: Record<Env, string> = { dev: "개발", staging: "검수", prod: "상용" };
const DEBOUNCE_MS = 400;

// 시나리오 저작 도구(별도 frontend 서비스) URL.
// 배포 환경별로 다르므로 env 로 주입한다. 미설정 시 로컬 dev 포트로 폴백.
const SCENARIO_BUILDER_URL =
  import.meta.env.VITE_SCENARIO_BUILDER_URL ?? "http://localhost:5173";

// ── 필터 상태 타입 ────────────────────────────────────────────────────────────
type PublishedFilter = "all" | "published" | "draft";

// ── DeployModal ───────────────────────────────────────────────────────────────
interface DeployModalState { sc: Scenario; env: Env }

function DeployModal({ state, onClose, onDeploy }: {
  state: DeployModalState;
  onClose: () => void;
  onDeploy: (env: Env, note: string) => void;
}) {
  const [note, setNote] = useState("");
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">🚀 시나리오 배포</div>
        <div className="modal-body">
          <div>
            <div className="form-label">시나리오</div>
            <div style={{ fontWeight: 700 }}>{state.sc.name}</div>
            <div style={{ fontSize: 12, color: "var(--text-3)", fontFamily: "monospace" }}>
              {state.sc.scenario_id} v{state.sc.version}
            </div>
          </div>
          <div>
            <div className="form-label">배포 환경</div>
            <span className={`env-badge ${state.env}`}>{ENV_LABEL[state.env]}</span>
          </div>
          <div>
            <div className="form-label">배포 메모 (선택)</div>
            <input
              className="input"
              placeholder="예) 요금 조회 플로우 개선"
              value={note}
              onChange={(e) => setNote(e.target.value)}
              autoFocus
            />
          </div>
          {state.env === "prod" && (
            <div style={{
              padding: "10px 12px", background: "#7f1d1d20",
              border: "1px solid var(--red-dim)", borderRadius: 6,
              fontSize: 12, color: "var(--red)",
            }}>
              ⚠ 상용 배포입니다. 검수 환경에서 충분히 검증 후 진행하세요.
            </div>
          )}
        </div>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>취소</button>
          <button
            className={`btn ${state.env === "prod" ? "btn-danger" : "btn-primary"}`}
            onClick={() => onDeploy(state.env, note)}
          >
            배포
          </button>
        </div>
      </div>
    </div>
  );
}

// ── TestModal ─────────────────────────────────────────────────────────────────
interface TestModalState { sc: Scenario }

function TestModal({ state, onClose, onTest }: {
  state: TestModalState;
  onClose: () => void;
  onTest: (phone: string, asr: string) => void;
}) {
  const [phone, setPhone] = useState("+821012345678");
  const [asr,   setAsr]   = useState("안녕하세요");
  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-title">🧪 시나리오 테스트</div>
        <div className="modal-body">
          <div>
            <div className="form-label">시나리오</div>
            <div style={{ fontWeight: 700 }}>{state.sc.name}</div>
            <div style={{ fontSize: 12, color: "var(--text-3)", fontFamily: "monospace" }}>
              {state.sc.scenario_id} v{state.sc.version}
            </div>
          </div>
          <div>
            <div className="form-label">테스트 발신 번호</div>
            <input className="input" value={phone} onChange={(e) => setPhone(e.target.value)} autoFocus />
          </div>
          <div>
            <div className="form-label">Mock ASR 첫 발화</div>
            <input className="input" value={asr} onChange={(e) => setAsr(e.target.value)} />
          </div>
        </div>
        <div className="modal-actions">
          <button className="btn btn-ghost" onClick={onClose}>취소</button>
          <button className="btn btn-primary" onClick={() => onTest(phone, asr)}>테스트 시작</button>
        </div>
      </div>
    </div>
  );
}

// ── Toast ─────────────────────────────────────────────────────────────────────
function Toast({ msg, onClose }: { msg: string; onClose: () => void }) {
  useEffect(() => {
    const id = setTimeout(onClose, 3_500);
    return () => clearTimeout(id);
  }, [onClose]);
  return (
    <div style={{
      position: "fixed", bottom: 24, right: 24, zIndex: 200,
      background: "var(--bg-3)", border: "1px solid var(--green)",
      borderRadius: 8, padding: "12px 16px",
      color: "var(--green)", fontSize: 13, fontWeight: 600,
      boxShadow: "0 8px 32px rgba(0,0,0,0.4)",
    }}>
      ✓ {msg}
    </div>
  );
}

// ── 필터 chip ─────────────────────────────────────────────────────────────────
function FilterChip({ label, active, onClick }: {
  label: string; active: boolean; onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: "4px 12px", borderRadius: 12, fontSize: 12, fontWeight: 600,
        cursor: "pointer", border: "1px solid",
        background: active ? "var(--accent)" : "var(--bg-2)",
        color:      active ? "#fff" : "var(--text-2)",
        borderColor: active ? "var(--accent)" : "var(--border)",
        transition: "all 0.15s",
      }}
    >
      {label}
    </button>
  );
}

// ── 메인 컴포넌트 ─────────────────────────────────────────────────────────────
export default function Scenarios() {
  const [scenarios,    setScenarios]    = useState<Scenario[]>([]);
  const [total,        setTotal]        = useState(0);
  const [loading,      setLoading]      = useState(false);
  const [deployModal,  setDeployModal]  = useState<DeployModalState | null>(null);
  const [testModal,    setTestModal]    = useState<TestModalState   | null>(null);
  const [toast,        setToast]        = useState<string | null>(null);

  // ── 필터 상태 ────────────────────────────────────────────────────────────────
  const [searchInput,  setSearchInput]  = useState("");
  const [searchName,   setSearchName]   = useState("");  // debounce 결과
  const [pubFilter,    setPubFilter]    = useState<PublishedFilter>("all");
  const [tenantFilter, setTenantFilter] = useState("");
  const [tagFilter,    setTagFilter]    = useState("");

  const debounceTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // 고유 tenant 목록 (조회된 시나리오에서 추출)
  const tenants = Array.from(new Set(scenarios.map((s) => s.tenant_id))).sort();

  // ── 검색 debounce ────────────────────────────────────────────────────────────
  useEffect(() => {
    if (debounceTimer.current) clearTimeout(debounceTimer.current);
    debounceTimer.current = setTimeout(() => {
      setSearchName(searchInput.trim());
    }, DEBOUNCE_MS);
    return () => { if (debounceTimer.current) clearTimeout(debounceTimer.current); };
  }, [searchInput]);

  // ── 데이터 조회 ─────────────────────────────────────────────────────────────
  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await getScenarios({
        name:      searchName     || undefined,
        tenant_id: tenantFilter   || undefined,
        published: pubFilter === "all" ? undefined : pubFilter === "published",
        tag:       tagFilter      || undefined,
      });
      setScenarios(res.items);
      setTotal(res.total);
    } catch { /* 오류 시 기존 목록 유지 */ }
    finally { setLoading(false); }
  }, [searchName, tenantFilter, pubFilter, tagFilter]);

  useEffect(() => { void load(); }, [load]);

  // ── 배포 핸들러 ─────────────────────────────────────────────────────────────
  const handleDeploy = async (env: Env, note: string) => {
    if (!deployModal) return;
    await deployScenario(deployModal.sc.scenario_id, { env, note });
    setDeployModal(null);
    setToast(`${deployModal.sc.name} → ${ENV_LABEL[env]} 배포 완료`);
    await load();
  };

  // ── 테스트 핸들러 ────────────────────────────────────────────────────────────
  const handleTest = async (phone: string, asr: string) => {
    if (!testModal) return;
    await testScenario(testModal.sc.scenario_id, { phone_number: phone, mock_asr: asr });
    setTestModal(null);
    setToast(`테스트 발신 예약: ${phone}`);
  };

  // ── 필터 초기화 ─────────────────────────────────────────────────────────────
  const clearFilters = () => {
    setSearchInput("");
    setSearchName("");
    setPubFilter("all");
    setTenantFilter("");
    setTagFilter("");
  };
  const hasFilter = searchName || pubFilter !== "all" || tenantFilter || tagFilter;

  // ── 렌더 ─────────────────────────────────────────────────────────────────────
  return (
    <div>
      {/* 헤더 */}
      <div className="page-header">
        <div>
          <div className="page-title">시나리오 관리</div>
          <div className="page-sub">
            {loading ? "로딩 중…" : `총 ${total}개 시나리오`}
            {hasFilter && ` (필터 적용 중)`}
          </div>
        </div>
        <a
          href={SCENARIO_BUILDER_URL}
          target="_blank"
          rel="noreferrer"
          className="btn btn-primary btn-sm"
        >
          ✏ 저작 도구 열기
        </a>
      </div>

      {/* 검색 + 필터 바 */}
      <div style={{
        display: "flex", flexWrap: "wrap", gap: 10, alignItems: "center",
        marginBottom: 20,
        padding: "14px 16px",
        background: "var(--bg-2)", border: "1px solid var(--border)", borderRadius: 10,
      }}>
        {/* 이름 검색 */}
        <input
          className="input"
          style={{ flex: "1 1 200px", minWidth: 160 }}
          placeholder="시나리오 이름 검색…"
          value={searchInput}
          onChange={(e) => setSearchInput(e.target.value)}
        />

        {/* 게시 상태 필터 */}
        <div style={{ display: "flex", gap: 6 }}>
          {(["all", "published", "draft"] as const).map((f) => (
            <FilterChip
              key={f}
              label={f === "all" ? "전체" : f === "published" ? "게시됨" : "초안"}
              active={pubFilter === f}
              onClick={() => setPubFilter(f)}
            />
          ))}
        </div>

        {/* 테넌트 필터 */}
        {tenants.length > 1 && (
          <select
            className="input"
            style={{ flex: "0 0 160px" }}
            value={tenantFilter}
            onChange={(e) => setTenantFilter(e.target.value)}
          >
            <option value="">모든 테넌트</option>
            {tenants.map((t) => (
              <option key={t} value={t}>{t}</option>
            ))}
          </select>
        )}

        {/* 태그 필터 표시 */}
        {tagFilter && (
          <span
            style={{
              padding: "4px 10px", borderRadius: 12, fontSize: 12,
              background: "#1e3a5f", color: "#93c5fd",
              border: "1px solid #1e40af", cursor: "pointer",
            }}
            onClick={() => setTagFilter("")}
            title="클릭하면 태그 필터 해제"
          >
            🏷 {tagFilter} ✕
          </span>
        )}

        {/* 필터 초기화 */}
        {hasFilter && (
          <button className="btn btn-ghost btn-sm" onClick={clearFilters}>
            초기화
          </button>
        )}
      </div>

      {/* 시나리오 그리드 */}
      <div className="scenario-grid">
        {scenarios.map((sc) => (
          <div key={sc.scenario_id} className="sc-card">
            <div className="sc-card-header">
              <div style={{ flex: 1, minWidth: 0 }}>
                <div className="sc-name" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {sc.name}
                </div>
                <div className="sc-id">{sc.scenario_id}</div>
              </div>
              <span className={`badge ${sc.published ? "green" : "gray"}`}>
                {sc.published ? "게시됨" : "초안"}
              </span>
            </div>

            <div className="sc-meta">
              <span className="badge blue">v{sc.version}</span>
              <span className="badge gray">{sc.node_count} 노드</span>
              <span
                className="badge purple"
                style={{ cursor: "pointer" }}
                onClick={() => setTenantFilter(sc.tenant_id)}
                title={`${sc.tenant_id} 필터`}
              >
                {sc.tenant_id}
              </span>
              {sc.tags.map((t) => (
                <span
                  key={t}
                  className="badge gray"
                  style={{ cursor: tagFilter === t ? "default" : "pointer" }}
                  onClick={() => setTagFilter(t === tagFilter ? "" : t)}
                  title={`'${t}' 태그 필터`}
                >
                  {t}
                </span>
              ))}
            </div>

            {/* 환경별 배포 버전 */}
            <div className="sc-env-row">
              {ENVS.map((env) => (
                <span key={env} className={`sc-env-pill ${env}`}>
                  {env[0].toUpperCase()} {sc.env_deployed[env] ?? "미배포"}
                </span>
              ))}
              <span style={{ marginLeft: "auto", fontSize: 11, color: "var(--text-3)" }}>
                {sc.updated_at.slice(0, 10)}
              </span>
            </div>

            <div className="sc-actions">
              <button
                className="btn btn-ghost btn-sm"
                onClick={() => setTestModal({ sc })}
              >
                🧪 테스트
              </button>
              {ENVS.map((env) => (
                <button
                  key={env}
                  className="btn btn-ghost btn-sm"
                  style={{ fontSize: 11 }}
                  onClick={() => setDeployModal({ sc, env })}
                >
                  → {ENV_LABEL[env]}
                </button>
              ))}
            </div>
          </div>
        ))}
      </div>

      {/* 빈 상태 */}
      {scenarios.length === 0 && !loading && (
        <div style={{ textAlign: "center", padding: "48px 0", color: "var(--text-3)", fontSize: 13 }}>
          {hasFilter ? "검색 결과가 없습니다." : "시나리오가 없습니다."}
          {hasFilter && (
            <div style={{ marginTop: 8 }}>
              <button className="btn btn-ghost btn-sm" onClick={clearFilters}>
                필터 초기화
              </button>
            </div>
          )}
        </div>
      )}

      {deployModal && (
        <DeployModal
          state={deployModal}
          onClose={() => setDeployModal(null)}
          onDeploy={(e, n) => void handleDeploy(e, n)}
        />
      )}
      {testModal && (
        <TestModal
          state={testModal}
          onClose={() => setTestModal(null)}
          onTest={(p, a) => void handleTest(p, a)}
        />
      )}
      {toast && <Toast msg={toast} onClose={() => setToast(null)} />}
    </div>
  );
}
