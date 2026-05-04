# AgentOE 스프린트 종료 리포트 — Track 3 + Track 4

작성일: 2026-04-19

## 요약

지난 스프린트에서 Track 1 (에이전틱 단위테스트·CI) 과 Track 2 P1 Top 5 (보안·과금·로그)
를 skeleton 에 반영한 데 이어, 이번 스프린트는 **관측성(Track 3)** 과 **GUI 시나리오 빌더
(Track 4)** 두 축을 완성한다.

- **Track 3** — `agentoe_llm_quota_*` 카운터와 `agentoe_jwks_*` 게이지·히스토그램을
  메트릭 모듈에 추가하고, `quota.py` / `llm_service.py` / `jwks_cache.py` 세 곳에 훅을
  심었다. 사이드카 없이 `/metrics/prometheus` 단일 엔드포인트로 내보낸다.
- **Track 4** — 기존 HTML 목업을 제거하고 **React 18 + Vite + TypeScript + React Flow 11**
  로 실제 번들되는 프런트엔드를 새로 만들었다. 백엔드는 `Scenario` DSL 을 버전 관리하는
  `scenarios` MongoDB 컬렉션 + REST API (`/api/v1/scenarios/*`) 를 갖추고,
  프런트와 1:1 일치하는 Zod 스키마로 양 끝에서 DSL 을 검증한다.

모든 수정 파일은 py_compile / tsc 없이도 import/런타임 수준에서 스모크 검증을 통과했다.

## Track 3 — 메트릭 보강 (quota + JWKS)

| 항목 | 파일 | 핵심 구현 |
|---|---|---|
| 3-A 메트릭 레지스트리 | `backend/app/core/metrics.py` | `_MetricsStore` 에 `llm_quota_checks`, `llm_tokens_consumed`, `llm_cost_cents`, `jwks_lookups`, `jwks_refresh_duration_s` 5종 추가. `_PrometheusMetrics` 에 대응 Counter/Histogram 정의. `_VALID_QUOTA_SCOPES/RESULTS`, `_VALID_JWKS_LOOKUP_RESULTS` 로 레이블 카디널리티 보호. |
| 3-A 퍼블릭 API | `metrics.py` | `record_quota_check()`, `record_llm_usage()`, `record_jwks_lookup()`, `record_jwks_refresh()` — 모두 `_store` (fallback) + `_prom` (표준 경로) 이중 기록. |
| 3-A 폴백 텍스트 | `metrics.py` | Prometheus client 부재 시도 `agentoe_llm_quota_checks_total`, `agentoe_llm_tokens_consumed_total`, `agentoe_llm_cost_cents_total`, `agentoe_jwks_lookups_total`, `agentoe_jwks_refresh_duration_seconds{_bucket,_sum,_count}` 모두 수작업 export. |
| 3-B quota 훅 | `backend/app/core/quota.py` | `enforce_quota` 3-way 분기 지점 (정상 / warn / fallback·reject) 에서 각각 `record_quota_check` 호출. scope="none" 은 정상 경로 표지. |
| 3-B 토큰·비용 커밋 | `backend/app/services/llm_service.py` | `commit_usage()` 성공 직후 `record_llm_usage(tenant_id, model, tokens, cost_cents)` 호출. 비용은 model-specific `_estimate_cost_cents` 결과 재사용. |
| 3-C JWKS 훅 | `backend/app/core/jwks_cache.py` | `get_key()` 에서 cache-hit/miss/force-refresh/fail 4가지 결과를 `record_jwks_lookup` 로 분류 기록. `_refresh()` 래핑에서 `time.monotonic()` 경과를 `record_jwks_refresh(duration_s, success)` 로 히스토그램에 적재. |
| 3-D 단위 테스트 | `backend/tests/unit/test_metrics_track3.py` | 4개 API × scope/result 조합, 레이블 정규화, 폴백 텍스트 마커 존재 검증 — 총 11 케이스. |

### 스모크 검증

로컬 러너로 Track 3 전체 경로 실행 확인:

```
OK Track 3 metrics flow: all 4 APIs + Prometheus export verified
  quota_checks records : 4
  llm_tokens records   : 1
  jwks_lookups records : 2
  jwks_refresh hist    : 2
```

`generate_prometheus_metrics()` 에서 `agentoe_llm_quota_checks_total`,
`agentoe_llm_tokens_consumed_total`, `agentoe_llm_cost_cents_total`,
`agentoe_jwks_lookups_total`, `agentoe_jwks_refresh_duration_seconds` 5종 모두 export 확인.

## Track 4 — GUI 시나리오 빌더 (React Flow)

### 4-A 프런트엔드 스캐폴딩
- `skeleton/frontend/` 신규. React 18.3.1 / Vite 5.4.10 / TypeScript 5.6.3 / React Flow 11.11.4 / Zod 3.23.8.
- `package.json`, `tsconfig.json` (paths `@/* → src/*`), `vite.config.ts` (`/api → localhost:8000` proxy), `vitest.config.ts`, `index.html`, `.gitignore` 모두 포함.
- 기존 HTML 목업은 의존성 없이 보관용 (참조 삭제됨).

### 4-B 타입 + DSL 직렬화
| 파일 | 역할 |
|---|---|
| `src/types/scenario.ts` | 8개 노드 (`intent/llm/tool/branch/transfer/wait/context/end`) 의 Zod discriminated union, `EdgeSchema`, `ScenarioLimitsSchema`, `ScenarioSchema.strict()`. `NODE_ID = /^[a-zA-Z0-9_\-]+$/u`, `SCENARIO_ID = /^[a-z0-9_\-]+$/u` — 백엔드 Pydantic 정규식과 1:1. |
| `src/lib/dsl.ts` | `toGraph(scenario, {positions})` / `fromGraph({graph, meta})` 라운드트립, `defaultNodeConfig(type)` 타입별 기본값, `uniqueNodeId`, `validateGraph` (6종 issue code: DUPLICATE_NODE_ID, ENTRY_MISSING, FALLBACK_MISSING, EDGE_FROM_MISSING, EDGE_TO_MISSING, UNREACHABLE_NODE). fallback_node 는 도달성 검사에서 예외 처리. |
| `src/lib/api.ts` | `listScenarios / getScenario / saveScenario / publishScenario / validateScenario` — `Authorization: Bearer` + optional `X-Tenant-Id`. |

### 4-C 빌더 UI
- `src/App.tsx` — `useNodesState/useEdgesState`, 팔레트 drag → `onDrop` (`application/agentoe-node`), `onConnect` (edge 생성), 노드 id 변경 시 엣지·entry·fallback_node 참조 자동 cascade, 실시간 `validateGraph` 이슈 패널.
- `src/components/ScenarioNode.tsx`, `Palette.tsx`, `PropertyPanel.tsx` (type-specific ConfigEditor 8종 switch), `ValidationPanel.tsx`.

### 4-D 백엔드 시나리오 REST API
- `backend/app/api/v1/routers/scenarios.py` — `GET /` (list by tenant), `POST /` (201, 서버 채번), `GET /{id}?version=latest|published|int`, `POST /{id}/publish` (admin), `POST /validate` (저장 없음), `DELETE /{id}/versions/{v}` (admin).
- `payload["tenant_id"] = tenant.tenant_id` 강제 덮어쓰기 — payload 변조로 다른 테넌트에 쓰기 차단. Pydantic ValidationError → 422 `{code: "DSL_VALIDATION_ERROR", errors: [...]}`.
- `backend/app/api/v1/__init__.py` 에 라우터 등록.

### 4-E 시나리오 Repository (MongoDB)
- `backend/app/repositories/scenario_repository.py` — `ScenarioRepository` 클래스.
- 키: `(tenant_id, scenario_id, version)` 조합 유일. 인덱스는 `mongo/init-tenants.js` 참조.
- `save()` — 동일 scenario_id 최신 버전 조회 후 `+1`, `published=False` 강제. 클라이언트 `version` 필드는 무시.
- `publish()` — ① 대상 버전 존재 확인 → ② 기존 published=True 전부 False 로 demote → ③ 대상을 True 로 promote + `updated_at` 갱신. 경합 감지 시 `ScenarioConflictError`.
- `delete_version()` — published=True 버전은 `ScenarioConflictError(409)`, draft 만 삭제.
- `list_by_tenant()` — aggregation pipeline 으로 scenario_id 별 최신만 반환, `include_drafts=False` 옵션.

### 4-F 빌더 테스트 + 검증 코드 경로

#### 백엔드 Repository 단위 테스트 — `tests/unit/test_scenario_repository.py` (9 케이스)
| 케이스 | 커버 |
|---|---|
| `test_save_increments_version` | +1 채번, 모든 저장은 draft |
| `test_get_latest_returns_highest_version` | `sort=[("version",-1)]` 경로 |
| `test_publish_toggles_single_published_version` | demote-then-promote 로직, 이전 published 자동 False |
| `test_publish_missing_version_raises_not_found` | 존재하지 않는 버전 → 404 |
| `test_get_published_when_none_raises` | draft 뿐일 때 404 |
| `test_delete_published_version_raises_conflict` | 409 거부 |
| `test_delete_draft_version_succeeds` | 204 + 이후 조회 404 |
| `test_tenant_isolation` | 다른 테넌트 데이터 간섭 없음, 동일 scenario_id 도 테넌트별 독립 버전 |
| `test_list_by_tenant_dedups_to_latest` | scenario_id 별 최신만 반환 |

#### 백엔드 라우터 통합 테스트 — `tests/integration/test_scenarios_api.py` (20 케이스)
FastAPI TestClient + `dependency_overrides` 로 `ScenarioRepository` 와 `get_current_tenant` / `require_roles` 를 우회:
- 저장 경로: 201 + 서버 채번, 연속 저장 시 version +1, DSL 오류 422.
- **테넌트 스푸핑 차단**: payload 에 `tenant_id="t_other"` 가 와도 JWT claim `t_acme` 로 덮어씀을 문서와 repo 양쪽에서 재확인.
- 조회: `version=latest|published|int`, invalid string 400, missing 404.
- 발행: admin 200, operator 403, missing 404, bad payload 400 (version 없음 / 음수).
- 삭제: draft 204, published 409, operator 403, missing 404.
- 목록: `include_drafts=False` 시 draft 제외.
- `/validate` 엔드포인트: ok / not-ok 각 분기.

#### 프런트엔드 DSL 라운드트립 테스트 — `frontend/src/lib/dsl.test.ts` (11+ 케이스, vitest)
| 케이스 그룹 | 커버 |
|---|---|
| **round-trip** | `toGraph → fromGraph` 시 nodes id/type/config 완전 보존, edges from/to/when/label 보존, `positions` 주입 시 덮어쓰기, 5개 노드 타입 혼합 시나리오 통과 |
| **defaultNodeConfig** | 8개 노드 타입별 기본 config 가 `ScenarioSchema.parse()` 통과 (tool 은 `tool_name="demo"` 보정), 알 수 없는 타입 → 런타임 에러 |
| **uniqueNodeId** | 빈 집합 `_1`, 연속 충돌 시 다음 빈 번호, 홀수 공백 시 가장 작은 빈 번호 |
| **validateGraph** | 정상 그래프 0건, DUPLICATE_NODE_ID, ENTRY_MISSING, FALLBACK_MISSING, EDGE_FROM_MISSING, EDGE_TO_MISSING, UNREACHABLE_NODE(warning, fallback 예외), entry 누락 시 도달성 검사 스킵 |

### 스모크 검증

로컬 러너로 Track 4 repository 전 경로 실행 확인:

```
OK all repository behaviors verified
  (save +1, publish demote-then-promote, get_published, delete published→409,
   delete draft→None, list_by_tenant dedup latest, include_drafts=False excludes,
   tenant isolation enforced)
```

Scenario DSL (`Scenario(**body)`) Pydantic 검증, `ScenarioRepository` 의
save/publish/get_latest/get_published/delete_version/list_by_tenant 전 경로가
인메모리 `_FakeCollection` 위에서 정상 동작함을 확인.

## 파일 변경 요약

### 신규 (26 파일)
- Track 3: `tests/unit/test_metrics_track3.py`
- Track 4 백엔드: `app/repositories/scenario_repository.py`, `app/api/v1/routers/scenarios.py`, `tests/unit/test_scenario_repository.py`, `tests/integration/test_scenarios_api.py`
- Track 4 프런트: `frontend/{package.json, tsconfig.json, vite.config.ts, vitest.config.ts, index.html, .gitignore}`,
  `frontend/src/{main.tsx, App.tsx, index.css}`,
  `frontend/src/types/scenario.ts`,
  `frontend/src/lib/{dsl.ts, api.ts, dsl.test.ts}`,
  `frontend/src/components/{ScenarioNode.tsx, Palette.tsx, PropertyPanel.tsx, ValidationPanel.tsx}`

### 수정 (5 파일)
- `app/core/metrics.py` — Track 3 메트릭 등록 + 퍼블릭 API + 폴백 텍스트
- `app/core/quota.py` — `record_quota_check` 3-way 훅
- `app/services/llm_service.py` — `record_llm_usage` 훅
- `app/core/jwks_cache.py` — lookup/refresh 분류 + duration 측정
- `app/api/v1/__init__.py` — `scenarios` 라우터 include

## 검증 결과

| 항목 | 상태 |
|---|---|
| Track 3 4개 API + Prometheus export 런타임 스모크 | ✅ 통과 |
| Track 4 ScenarioRepository 전 경로 런타임 스모크 | ✅ 통과 |
| Track 4 Scenario DSL Pydantic 검증 | ✅ 통과 |
| 기존 Track 1/2 테스트 회귀 영향 | 신규 파일 추가만, 기존 변경 없음 → 회귀 0 |

## 잔여 / 다음 스프린트 후보

1. **프런트엔드 E2E** — Playwright 로 `scenarios/` 라우터와의 실사용 플로우 (저장 → 발행 → 재조회) 커버.
2. **Prometheus alerting rules** — `agentoe_llm_quota_checks_total{result="reject"}` 급증, `agentoe_jwks_refresh_duration_seconds_bucket` tail latency 대한 경보 정의.
3. **JWKS kid 회전 Runbook** — Track 3-C 에서 새로 생긴 `force_refresh` 카운트를 운영 대시보드에 꽂고, 급증 시 대응 절차 기록.
4. **시나리오 빌더 UX** — (a) 인텐트 라벨 자동 제안, (b) edge `when` 표현식 검증, (c) published 버전 diff 뷰, (d) 롤백 (publish 이전 버전).
5. **Audit log** — `scenarios.publish` / `scenarios.delete_version` 를 `audit_log` 컬렉션에 기록 (actor client_id + 이전·이후 version 포함).
