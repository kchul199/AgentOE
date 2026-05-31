# HANDOFF — agentoe monorepo 세션 인계 문서

> **이 문서를 첫 5분 안에 다 읽어라.** 새 세션이 작업을 이어받을 때 필요한 모든 컨텍스트가 여기 있다.
> 마지막 업데이트: 2026-05-14 · **Phase N N5 전체 완료 (N5.1~N5.3). N1~N5 모두 완료 → Phase N 전체 완료. 다음: staging 실배포 → prod cutover.**
> 이전: 2026-05-14 Phase N N4 전체 완료 (N4.1~N4.3).

## 0. TL;DR

- 프로젝트: **agentoe** — 멀티 테넌트 Agentic Callbot. **단일 monorepo** (옛 AgenticOE_v2 + vbgw_v2 통합 완료).
- 단일 진실 소스 4개:
  - **`CLAUDE.md`** (프로젝트 규칙 3가지) — 절대 어기지 말 것.
  - **이 `HANDOFF.md`** — 현재 상태 / 다음 액션.
  - **`docs/reference/slo.md`** — SLO 임계치.
  - **`contracts/proto/voicebot.proto`** — vbgw bridge ↔ AgentOE backend gRPC contract (canonical).
- 코드 루트: `services/` (backend, frontend, vbgw-ai, vbgw-bridge, vbgw-orchestrator, freeswitch).
- **현재 상태: Phase 1-3 + X/Y/Z/D + M + T + SIPp 실콜 인프라 + 웹 포탈 E2E 테스트 모두 완료.** vbgw cutover 실행 + 운영 배포 단계만 남음. 다음 후보 §6.

## 1. 절대 규칙 (CLAUDE.md)

1. **Performance First** — 모든 I/O (STT, LLM, DB) 는 async/await, non-blocking.
2. **Latency is King** — 실시간 콜봇. 불필요한 루프/지연 금지.
3. **Error Handling** — 도구 호출(Tool Calling) 실패 시 통화 끊지 마라 — 폴백 시나리오 필수.

## 2. 디렉토리 지도 (monorepo 후)

```
agentoe/                              # 단일 monorepo
├── README.md, CLAUDE.md, Makefile, .gitignore
│
├── services/                         # ★ 모든 실 서비스
│   ├── backend/                      # Python FastAPI (multi-tenant, agentic, gRPC server)
│   │   ├── app/grpc_server/          # VoicebotAiService 구현 (Phase Y)
│   │   └── app/grpc_stubs/voicebot/  # vendored proto stub
│   ├── frontend/                     # React SPA
│   ├── vbgw-ai/                      # Go AI engine (legacy go-openai — cutover 후 deprecate)
│   ├── vbgw-bridge/                  # Go WS↔gRPC bridge
│   ├── vbgw-orchestrator/            # Go ESL/Redis 통화 라우팅
│   ├── freeswitch/                   # FS Dockerfile + dialplan
│   └── _test-stub/                   # backend dev 용 mock vbgw (Python)
│
├── contracts/                        # ★ canonical proto (sync 폐지)
│   ├── proto/voicebot.proto
│   ├── gen/{python,go}/voicebot/     # CI 가 drift 검증
│   └── Makefile
│
├── deploy/
│   ├── terraform/                    # AWS infra
│   ├── k8s-bootstrap/                # ALB ctrl, cert-manager, ESO, kps, Karpenter
│   ├── helm/{agentoe-backend,agentoe-frontend,vbgw}/
│   └── observability/dashboards/
│
├── docker/                           # 통합 docker-compose
│   ├── compose.backend{.yml,.dev.yml}
│   ├── compose.vbgw{.yml,.canary.yml,.prod.yml}
│   └── compose.integration.yml
│
├── docs/
│   ├── HANDOFF.md                    # ← 이 파일
│   ├── business/                     # 한국어 docx/xlsx (옛 AgenticOE_v2/docs)
│   ├── adr/, guide/, reference/slo.md, runbook/, performance/, reports/
│
├── scripts/integration/              # smoke_grpc_client.py, dev-integration.sh
├── mongo/, nginx/                    # init scripts
├── legacy/                           # 옛 vbgw C++ PJSIP (참조)
└── .github/{workflows,actions}/
```

## 3. 무엇이 이미 만들어졌나

### Phase 1-3 (#1-51): 인프라 + CI/CD + Observability
Terraform 33파일, k8s-bootstrap, Helm 3차트, GHA 5워크플로 + 5composite, github-oidc 모듈, SLO 65 PrometheusRules, Alertmanager Slack/PagerDuty, Grafana 4대시보드, alert-response runbook 3개.

### Phase X-D (#53-75): proto + backend gRPC + cutover infra + dev test
- contracts/proto/voicebot.proto canonical
- services/backend/app/grpc_server/ — VoicebotAiService 구현
- deploy/helm/vbgw/templates/deployment-bridge.yaml — canary block (Phase Z)
- vbgw-ai-cutover.md 4-stage runbook
- docker-compose 통합 + smoke gRPC client + dev-integration.sh

### Phase M (#77-98): monorepo 통합 ★ 이번 phase
- `git subtree add --squash=false` 로 vbgw_v2 의 34 commits 보존
- skeleton/ 폐지 → 루트로 끌어올림
- services/ 7개 서비스 통합 (backend/frontend/vbgw-ai/vbgw-bridge/vbgw-orchestrator/freeswitch/_test-stub)
- Go module path → `github.com/kchul199/agentoe/services/<name>`
- proto 단일화 (3 곳 중복 → contracts/ 한 곳)
- docker-compose → docker/ 디렉토리
- CI workflow path-filter / matrix 5-services + go-build job + contracts-gen drift 검증
- HANDOFF / cross-project / CLAUDE 통합

### Phase T: 로컬 전수 테스트 (Phase 3-5) ★ 최신 완료

**목표**: 인프라 없이 services/backend 코드 품질을 end-to-end 로 검증.

| Phase | 범위 | 결과 | 파일 |
|-------|------|------|------|
| Phase 3 (통합) | 67 tests — auth/sessions/scenarios/metrics/health/ws | **67/67 PASS** | `tests/integration/` |
| Phase 4 (E2E 기능) | 6 tests — F-02/03/04/05/09/10 | **6/6 PASS** | `tests/integration/test_e2e_functional.py` |
| Phase 5 (성능/SLO) | 6 tests — P5-01~06 | **6/6 PASS** | `tests/performance/test_slo_compliance.py` |

**Phase 5 실측 레이턴시 (CI 환경)**:

| 시나리오 | P50 | P95 | P99 | 성공률 | SLO 기준 |
|---------|-----|-----|-----|--------|---------|
| P5-01 미인증 ×50 | 55ms | 60ms | 61ms | 100% | 5xx=0 |
| P5-02 metrics/pipeline ×100 | 151ms | 156ms | 157ms | 100% | P95≤500ms |
| P5-03 metrics/sessions ×100 | 128ms | 134ms | 135ms | 100% | P95≤500ms |
| P5-04 scenarios/validate ×100 | 274ms | 282ms | 282ms | 100% | P95≤500ms |
| P5-05 혼합 ×150 | 239ms | 248ms | 249ms | 100% | P95≤500ms |
| P5-06 WS setup ×20 동시 | 30ms | 58ms | 60ms | 100% | P95≤500ms |

**주요 버그 픽스 (Phase T 중 발견)**:
- `app/api/v1/routers/vbgw.py`: Python 3.10 에서 `asyncio.TimeoutError ≠ builtins.TimeoutError` — `except (TimeoutError, asyncio.TimeoutError):` 로 수정 (실 프로덕션 버그).

**테스트 설계 주의사항** (다음 세션 참고):
- `TestClient(app)` 는 lifespan 을 트리거 → `app.main.init_db` (로컬 바인딩) 를 직접 패치해야 함. `app.core.database.init_db` 패치만으로는 부족.
- `httpx.AsyncClient(transport=ASGITransport)` 는 lifespan 을 트리거하지 않음.
- `asyncio.TimeoutError` 는 Python 3.10 에서 `builtins.TimeoutError` 와 무관한 별도 계층 (`concurrent.futures.TimeoutError` 하위). `except (TimeoutError, asyncio.TimeoutError):` 패턴 사용.

### Phase T++: 웹 포탈 E2E 테스트 인프라 ★ 최신 완료

**목표**: 관리 포탈 / 모니터링 포탈 등 웹 페이지 동작 전수 검증 (Docker stack 없이도 실행 가능).

| 파일 | 역할 |
|------|------|
| `services/frontend/tests/e2e/test_portal_smoke.py` | HTTP-only 스모크 테스트 31개 (requests 라이브러리, 브라우저 불필요) |
| `services/frontend/tests/e2e/test_web_portals.py` | Playwright 브라우저 E2E 테스트 (W-01~W-10) |
| `services/frontend/tests/e2e/run_test_server.py` | Mock backend 서버 (MongoDB/Redis/gRPC 전부 AsyncMock 패치 후 uvicorn 기동) |

**스모크 테스트 검증 범위**:

| 클래스 | 대상 | 테스트 수 |
|--------|------|-----------|
| `TestBackendAPI` | FastAPI `:8000` — health/readyz/openapi/swagger/auth/JWT/metrics/CORS/SLO | 13 |
| `TestFrontendSPA` | React SPA `:3000` — HTML/root element/static assets/SPA routing/SLO | 5 |
| `TestOrchestrator` | vbgw Orchestrator `:8080` — live/ready/metrics/active_calls/SLO | 5 |
| `TestNginxGateway` | Nginx Gateway `:80` — api routing/frontend/404/SLO | 4 |
| `TestGrafana` | Grafana `:3001` — health/dashboards API/frontend/SLO | 4 |

**실행 방법**:
```bash
# Mock server 기동 후 Backend 스모크 테스트 (인프라 없이)
cd services/frontend/tests/e2e
MONGODB_URI=mongodb://localhost:27017/test REDIS_URL=redis://localhost:6379 \
JWT_SECRET=test GROQ_API_KEY=test GOOGLE_APPLICATION_CREDENTIALS=/tmp/fake.json \
PYTHONPATH=$(pwd)/../../../backend python3 run_test_server.py --port 18000 &
BACKEND_URL=http://127.0.0.1:18000 python3 -m pytest test_portal_smoke.py::TestBackendAPI -v

# 풀스택 실행 (docker-compose 기동 후)
BACKEND_URL=http://localhost:8000 FRONTEND_URL=http://localhost:3000 \
ORCHESTRATOR_URL=http://localhost:8080 GRAFANA_URL=http://localhost:3001 \
python3 -m pytest test_portal_smoke.py -v

# Playwright E2E (브라우저, 풀스택 필요)
pip install playwright && playwright install chromium
python3 -m pytest test_web_portals.py -v
```

**검증 결과 (mock server 기준)**:
- `TestBackendAPI` — **13/13 PASS** (mock server 대상)
- Frontend/Orchestrator/Nginx/Grafana — 서비스 미기동 시 autouse fixture 가 SKIP 처리 (우아한 건너뜀)

**mock server 핵심 패치 (반드시 필요)**:
- `app.middleware.rate_limit_middleware.rate_limit_check` — Redis 없이 모든 요청 500 방지
- `app.domain.kill_switch.KillSwitchService.is_active` — Redis 없이 모든 요청 500 방지
- `app.main.init_db` (로컬 바인딩!) / `app.main.init_redis` — lifespan 바인딩 패치

### Phase N: 통합 운영포탈 (Operations Portal) ★ N5 완료 (2026-05-14) — Phase N 전체 완료

**목표**: 운영자가 모니터링/환경설정/상담이력/로그트레이스 + 추가운영기능을 한 곳에서 처리하는 웹 포탈 (services/portal/).

**plan 문서 (단일 진실)**: `docs/guide/phase-N-ops-portal-plan.md` (v2.1 — NG1/NG2/NG3 closing 반영).

**핵심 결정**:
- 위치: `services/portal/` 신규 (별도 Helm 차트, **internal ALB**).
- 인증: 자체 — MongoDB `portal_users` + bcrypt + TOTP MFA + refresh rotation. JWT `iss="agentoe-portal"`.
- 실시간: SSE 4채널 (Prom poll = metrics, Redis pub = audit/sessions, AM poll→Redis = alerts).
- RBAC: **issuer 격리** — `require_portal_role()` + `portal:viewer/operator/admin`. **자동 매핑 폐기**.

**Sub-phase 분해**:

| Phase | 산출 | task 갯수 |
|-------|------|----------|
| N0 ✅ | plan v2.1 (NG closing 2회) | 1 |
| N1 (진행 중) | 12 sub-task — 의존성 chain | 12 |
| N2-N5 | 모니터링/설정 → 이력/트레이스 → 추가기능 → 배포 | 30+ |

**N1 완료 (2026-05-13)**:
- N1.1 ✅ audit_events TS 호환 인덱스 4개 + portal_users 컬렉션
- N1.2 ✅ RBAC issuer 격리 (`require_portal_role()`, `iss="agentoe-portal"`)
- N1.3 ✅ audit_emitter (Mongo TS + Redis pub, graceful degrade) + emit 5곳 + unit 13/13 PASS
- N1.4 ✅ SseBroadcaster (Redis SUBSCRIBE, asyncio.Queue fan-out, 지수 백오프) + AmPoller (Redis SETNX leader election)
- N1.5 ✅ SSE 4채널 엔드포인트 (`/stream/metrics|sessions.active|audit.tail|alerts`)
- N1.6 ✅ AM proxy (GET/POST/DELETE) + NetworkPolicy (portal namespace ingress 강화)
- N1.7 ✅ 운영자 인증 (login rate-limit → bcrypt → TOTP → refresh rotation → CSRF double-submit)
- N1.8 ✅ sessions turns API (aggregate pipeline, limit/offset 200 per page)
- N1.9 ✅ CORS 확장 (PORTAL_ORIGIN, X-CSRF-Token, X-Env-Target, Last-Event-ID 허용)
- N1.10 ✅ dump_openapi.py + CI `openapi-lint` job (7-day artifact)
- N1.11 ✅ portal Vite scaffold — csrf.ts, auth.ts, sse.ts, AuthProvider, SSEProvider, LoginPage, main.tsx, App.tsx
- N1.12 ✅ SSE 연결 가드 — Semaphore(SSE_MAX_CONNECTIONS_PER_POD) 2차 방어, uvicorn UVICORN_LIMIT_CONCURRENCY 1차, ALB idle_timeout=3600 모두 적용

**N1 산출물 위치**:
- `services/backend/app/domain/audit_emitter.py` — audit emit
- `services/backend/app/domain/sse_broadcaster.py` — Redis SSE fan-out
- `services/backend/app/workers/am_poller.py` — AM leader election poller
- `services/backend/app/api/v1/routers/stream.py` — SSE 4채널 + N1.12 가드
- `services/backend/app/api/v1/routers/auth_portal.py` — 운영자 인증
- `services/backend/app/api/v1/routers/admin.py` — AM proxy 추가
- `services/backend/scripts/dump_openapi.py` — OpenAPI dump
- `services/ops-portal/src/` — portal 프론트엔드 scaffold
- `deploy/helm/values/{prod,staging}/backend.values.yaml` — SSE_MAX_CONNECTIONS_PER_POD
- `deploy/helm/agentoe-backend/templates/deployment.yaml` — UVICORN_LIMIT_CONCURRENCY 주입
- `deploy/k8s-bootstrap/manifests/network-policy-backend.yaml` — portal namespace ingress

**N5 완료 (2026-05-14) ★ N5 전체 완료 = Phase N 완료**:
- N5.1 ✅ `services/backend/app/api/v1/routers/admin.py` — Config 3 endpoints: `GET /admin/config/diff`, `GET /admin/config/{env}`, `PUT /admin/config/{env}`. RBAC: GET=viewer+, PUT dev/staging=operator+, PUT prod=admin only. audit emit, MongoDB `portal_configs` 컬렉션, upsert semantics.
- N5.1 ✅ `services/ops-portal/src/lib/api.ts` — `EnvConfig`, `ConfigDiff`, `ConfigDiffResponse`, `ConfigUpdateBody` 타입 추가. `getConfig(env)`, `getDiff()`, `updateConfig(env, body)` 함수 추가.
- N5.1 ✅ `services/ops-portal/src/pages/Config.tsx` — 하드코딩 `updated_by: "charls"` 제거 → `username` from `useAuth()`. `reload()` 분리, `canEdit(env)` RBAC 함수, 저장 오류 상태 `saveErr`. getDiff → `df.diffs` 구조 분해.
- N5.1 ✅ `services/ops-portal/src/providers/AuthProvider.tsx` — `AuthState.username: string | null` 추가. `login()` 시 `username` 저장. `useAuth()` 에서 노출.
- N5.2 ✅ `services/backend/app/infra/kms_client.py` — 신규: `kms_generate_data_key()`, `kms_decrypt_dek()`, `pack_kms_payload()`, `unpack_kms_payload()`. boto3 executor 패턴 (aiobotocore 불필요). 저장 포맷: `2B len_dek | encrypted_dek | 12B nonce | aesgcm_ct`.
- N5.2 ✅ `services/backend/app/core/config.py` — `PORTAL_KMS_KEY_ID`, `PORTAL_KMS_REGION` 추가.
- N5.2 ✅ `services/backend/app/api/v1/routers/auth_portal.py` — `_encrypt_mfa_secret` / `_decrypt_mfa_secret` async KMS 버전으로 교체. `kms:` prefix 저장 포맷. 레거시 secret 자동 폴백 (무중단 마이그레이션). KMS 장애 시 env-var degraded mode 폴백.
- N5.2 ✅ `scripts/migrate_mfa_to_kms.py` — 신규: 기존 env-var 암호화 MFA secret → KMS 재암호화. `--dry-run` / `--no-dry-run` / `--username` 옵션.
- N5.3 ✅ `scripts/portal_prod_deploy.sh` — 5단계 게이트 (staging 헬스 / ECR CVE CRITICAL=0 / 수동승인 / canary 10%→50%→100% + Prometheus error_rate gate / smoke test + PD maintenance 종료). `--dry-run`, `--auto-approve`, `--skip-*` 옵션.
- N5.3 ✅ `deploy/helm/values/prod/portal.values.yaml` — replicaCount=3, cpu 500m, PodDisruptionBudget(minAvailable=2), SSE_MAX_CONNECTIONS_PER_POD=200.
- N5.3 ✅ `docs/runbook/portal-prod-deploy.md` — prod 배포 runbook (5단계 게이트 상세 / 수동 단계별 배포 / 롤백 / 트러블슈팅 / 배포후 체크리스트).
- N5.3 ✅ `.github/workflows/portal-build.yml` — `portal-prod-deploy` job 추가 (v* 태그 + GitHub Environment `production` 승인 게이트 + helm lint + 5단계 배포 스크립트 + job summary).

**함정 (N5 발견)**:
- `GET /admin/config/diff` 는 반드시 `GET /admin/config/{env}` 보다 먼저 선언해야 함 — FastAPI 라우트 섀도잉 방지. `_VALID_ENVS = {"dev", "staging", "prod"}` 로 env 값 검증 필수.
- KMS `_KMS_PREFIX = "kms:"` prefix 로 레거시/신규 포맷 구분. 복호화 시 prefix 유무로 자동 판별 → 마이그레이션 중 두 포맷 공존 가능.
- `portal_prod_deploy.sh` Canary 단계 중 error_rate Prometheus 쿼리는 `service="agentoe-portal"` 라벨 기준 — 실 환경에서 메트릭 라벨 확인 필요.
- `portal-prod-deploy` CI job 은 `concurrency.cancel-in-progress: false` — prod 배포 중 새 커밋 push 로 취소되지 않음.

**N4 완료 (2026-05-14) ★ N4 전체 완료**:
- N4.1 ✅ `services/ops-portal/src/pages/KillSwitch.tsx` — useSSE("ALERTS") 구독 + 30s 자동 새로고침 + SseStatusBadge + activated_by 표시 + RBAC-gated 토글 (viewer 읽기 전용)
- N4.1 ✅ `services/ops-portal/src/lib/api.ts` — `toggleKillSwitch` 객체 body로 시그니처 수정, `KillSwitch.activated_by` 필드 추가
- N4.2 ✅ `services/ops-portal/src/lib/api.ts` — `Env` 타입, `Scenario` 인터페이스 (tags + env_deployed), `ScenarioListParams`, `getScenarios(params?)`, `testScenario()`, `deployScenario()` 추가
- N4.2 ✅ `services/ops-portal/src/pages/Scenarios.tsx` — 이름 검색(debounce 400ms), published/draft/전체 필터 chip, tenant select 필터, 태그 클릭 필터, 빈 상태 처리, 타입 정합성 수정
- N4.3 ✅ `scripts/portal_staging_deploy.sh` — preflight(kubectl/helm/aws/ECR 이미지) → helm upgrade --atomic → rollout wait → smoke test(/healthz) → Slack 알림 (6단계 자동화)
- N4.3 ✅ `docs/runbook/portal-staging-deploy.md` — 배포 runbook (전제조건/빠른실행/수동단계/롤백/트러블슈팅/배포후 체크리스트)

**N3 완료 (2026-05-14) ★ N3 전체 완료**:
- N3.1 ✅ `services/ops-portal/src/pages/Alerts.tsx` — useSSE(ALERTS) + SilenceModal(matcher 자동추출, duration preset) + RBAC gate (portal:operator+)
- N3.1 ✅ `services/ops-portal/src/App.tsx` — alerts 라우트/네비 추가
- N3.2 ✅ `services/backend/app/core/metrics.py` — `get_metrics_snapshot_async()` 신규 (10 PromQL queries, asyncio.gather, 2s timeout, fallback → sync)
- N3.2 ✅ `services/backend/app/api/v1/routers/stream.py` — `run_in_executor` 제거 → `await get_metrics_snapshot_async()` 직접 호출 (CLAUDE.md Performance First 준수)
- N3.3 ✅ `.github/workflows/portal-build.yml` — portal 전용 CI (tsc typecheck, hadolint, helm-lint×{staging,prod}, kubeconform, ECR push, Trivy 이미지 스캔, portal-gate)
- N3.3 ✅ `.github/workflows/validate.yml` — portal 경로 감지 추가, helm-lint 매트릭스에 agentoe-portal 추가, hadolint 매트릭스에 ops-portal/Dockerfile 추가
- N3.3 ✅ `.github/workflows/build-images.yml` — ops-portal 서비스 감지 + matrix include 추가

**함정 (N3 발견)**:
- `get_metrics_snapshot_async()` 의 all-zero 감지: `ccu==0 and p95_ms==0 and total_calls==0` 이면 Prometheus 미연동으로 간주 → sync fallback. 실제 0 트래픽 상황과 구분 불가 — prod 에서는 Prometheus URL 환경변수 필히 설정.
- `portal-build.yml` 의 ESLint step: `.eslintrc*` 또는 `eslint.config*` 가 없으면 자동 skip (scaffold 단계에 eslint 설정 미완이어도 CI 깨지지 않음).

**N2 완료 (2026-05-14) ★ N2 전체 완료**:
- N2.1 ✅ `services/backend/app/core/metrics.py` — `get_metrics_snapshot()` (in-process KPI)
- N2.1 ✅ `services/backend/app/infra/prometheus_client.py` — PrometheusClient 싱글톤 (N3 실연동 준비)
- N2.1 ✅ `services/backend/app/api/v1/routers/admin.py` — `GET /admin/env/info` (git_sha, build_time, pod_name)
- N2.2 ✅ `services/ops-portal/src/lib/api.ts` — 실 backend `/api/v1` 연동 + CSRF auto-inject + 401 재시도
- N2.3 ✅ `services/ops-portal/src/pages/Dashboard.tsx` — useSSE(METRICS) SSE push + stale 감지(10s) + ReferenceLine SLO
- N2.4 ✅ `services/ops-portal/src/pages/Sessions.tsx` — getSessionTurns + useSSE(SESSIONS_ACTIVE) 라이브 배지
- N2.5 ✅ `services/ops-portal/src/pages/AuditPage.tsx` — 신규, useSSE(AUDIT_TAIL) + 무한 스크롤 + 필터 + trace 드릴다운
- N2.5 ✅ `services/ops-portal/src/App.tsx` — audit 라우트/네비 추가
- N2.6 ✅ `services/ops-portal/Dockerfile` — node build → nginx-unprivileged (N2.6)
- N2.6 ✅ `services/ops-portal/nginx.conf` — /api/v1/stream/* proxy_buffering off, SPA fallback
- N2.6 ✅ `services/ops-portal/docker-entrypoint.sh` — envsubst(BACKEND_UPSTREAM) → conf.d
- N2.6 ✅ `deploy/helm/agentoe-portal/` — Helm 차트 신규 (Chart.yaml, values.yaml, templates/ 8개)
- N2.6 ✅ `deploy/helm/values/{staging,prod}/portal.values.yaml` — internal ALB, 환경별 hostname/ACM

**함정 (N2 발견)**:
- `SseClient.reopen()` 호출 시 `lastEventId` 초기화 불필요 — SSEProvider 가 lastEventId 유지해야 gap 없음. 단 Dashboard 는 매 tick 독립이므로 무관.
- nginx `proxy_buffering off` 는 SSE 경로에만 적용. 일반 API 경로에는 buffering 유지 (성능).
- `agentoe-portal` Helm chart 는 `portal` namespace 에 배포. ConfigMap 의 `BACKEND_UPSTREAM` 값은 cross-namespace DNS (`.default.svc.cluster.local`) 사용.

**v2.1 NG closing**:
- **NG1** audit_events Time Series 컬렉션 → in-place migration 불가. 신규 필드 모두 `metadata.*` (metaField) 하위 + 인덱스 추가만.
- **NG2** RBAC 자동 매핑 폐기. `iss="agentoe-portal"` 강제 — 기존 admin 토큰의 portal 격리 누수 차단.
- **NG3** backend NetworkPolicy ingress 강화 — portal namespace podSelector 만 허용 + AM basic auth N1 산출물.

### Phase T+: SIPp 실콜 부하 테스트 인프라 ★ 최신 완료

**목표**: 실 FreeSwitch/vbgw 스택 대상 SIP 프로토콜 레벨 10 CPS × 10분 부하 검증.

| 파일 | 역할 |
|------|------|
| `services/freeswitch/tests/sipp/uac_10cps_audio.xml` | SIPp UAC 시나리오 (PCMU SDP, X-Tenant-ID 헤더, RTD 측정) |
| `docker/compose.sipp.yml` | SIPp Docker 서비스 + vbgw-net 연결 + 결과 볼륨 |
| `services/freeswitch/scripts/run_10cps_10min.sh` | 오케스트레이션 스크립트 (헬스체크 → SIPp 실행 → SLO 분석) |
| `services/freeswitch/scripts/parse_sipp_results.py` | stats.csv 파싱 → P50/P95/P99 + burn rate + 에러 버짓 리포트 |

**실행 방법 (vbgw 스택 기동 후)**:
```bash
# 로컬 sipp 바이너리 사용
./services/freeswitch/scripts/run_10cps_10min.sh --target 127.0.0.1:5060

# Docker 컨테이너 사용
./services/freeswitch/scripts/run_10cps_10min.sh --docker

# 결과 단독 분석
python3 services/freeswitch/scripts/parse_sipp_results.py \
  --stats services/freeswitch/results/<run_id>/stats.csv --json
```

**SLO 기준**: 성공률 ≥ 99.9%, P95 ≤ 500ms, burn rate < 14.4, 에러 버짓 소진 < 80%.

## 4. 통계

- 총 task: 98개. 거의 모두 closed.
- 코드/매니페스트 파일: 약 4,000개.
- Docker 이미지: 5개 (freeswitch 는 third-party).
- Helm 차트 3개, GHA 워크플로 5개.

## 5. 알아야 할 함정 / 결정사항

### 5.1 라벨 카디널리티
모든 Prometheus 시리즈 — 유한한 알려진 집합 라벨만. `http_metrics_middleware.py` 가 route 를 path template (`/api/v1/sessions/{id}`) 로만 박는 이유.

### 5.2 ECR IMMUTABLE
같은 sha 재푸시 = 빌드 실패 = 정상.

### 5.3 GitHub OIDC trust 의 sub 패턴
- `ecr_push`: `refs/heads/main` 또는 `refs/tags/v*`
- `eks_deploy`: `:environment:staging|production`

### 5.4 SLO retention 트릭
kps raw 7d 짧지만 recording rule 이 30d 비율 사전 합산.

### 5.5 vbgw 분담
- vbgw-ai: legacy go-openai (cutover 후 deprecate)
- vbgw-bridge: WS audio_fork ↔ backend gRPC client
- vbgw-orchestrator: ESL + Redis 통화 라우팅 + admin REST
- freeswitch: SIP/RTP signaling + media

### 5.6 backend 미들웨어 순서 (Starlette 역순)
outer-most = `HTTPMetricsMiddleware`. main.py 의 `add_middleware` 마지막이 가장 outer.

### 5.7 monorepo 통합 후 변경점 (Phase M)
- Cross-project sync 폐지 (Phase X 의 sync-to-vbgw.sh 더 이상 필요 X).
- Go module path = `github.com/kchul199/agentoe/services/<name>`.
- 모든 service 가 `github.com/kchul199/agentoe/contracts/gen/go/voicebot` 에서 stub import.
- Docker compose 모두 `docker/` (옛 외부 network bridge 통합 필요 없음).
- Helm vbgw chart 의 image: `your-registry/...` → `REPLACE_ECR_REGISTRY/agentoe-{env}/vbgw-{component}`.

### 5.8 contracts/gen 자동 검증
sync 폐지 후 CI 의 `contracts-gen` job 이 `make gen` 결과 git diff 검증. proto 수정 시 `cd contracts && make gen` 후 commit.

### 5.9 Helm release / namespace
- backend / frontend → namespace `agentoe[-staging]`
- vbgw → namespace `vbgw[-staging]` (분리)

### 5.10 services/_test-stub
opt-in dev 도구. 운영 배포 X. backend integration test 용.

## 6. 다음 단계 후보

| 후보                               | 무엇                                                                       | 의존성     |
|------------------------------------|----------------------------------------------------------------------------|------------|
| **staging 실배포** ★ 다음 ★        | `./scripts/portal_staging_deploy.sh` 실행 — ECR 이미지 존재 전제 | N5 ✅ 완료 |
| **prod 실배포**                    | `git tag v1.0.0 && git push origin v1.0.0` → CI portal-prod-deploy 자동 실행 | staging 완료 후 |
| **푸시 + PR open + main 머지**     | feat/monorepo-merge push → PR → CI green → merge commit (squash 금지)      | 사용자 host |
| **cutover Stage A (staging 100%)** | `./scripts/cutover/stage-a-staging.sh` — 자동 preflight + helm + smoke + 게이트 + 롤백 | 머지 후 |
| **prod cutover gates**             | Load → Chaos → DR drill → Security → On-call → 영업 (`docs/runbook/prod-cutover-gates.md`) | Stage A 후 |
| **prod cutover Stage B-D**         | 10% / 50% / 100% canary (`docs/runbook/vbgw-ai-cutover.md`)                | gates 통과 후 |
| **DR drill 분기 1회**              | `docs/runbook/disaster-recovery.md` §2 — Velero 복원 검증                    | prod 안정 후 |
| **Security hardening**             | Kyverno, Falco, cosign, Linkerd mTLS                                       | 인프라 안정 |
| **Load testing + Chaos**           | k6 + chaos-mesh (ASGI 레이어 SLO 검증 완료 — 실 k6 prod 부하는 Gate A)     | Phase T ✓  |
| **SIPp 실콜 부하 테스트**          | 10 CPS × 10분 시나리오 + Docker Compose + SLO 분석기 완료 (실 FS 스택 필요)  | Phase T ✓  |
| **Tracing (Tempo) + Loki**         | OTel exporter + Promtail                                                   | Phase 3 권장 |
| **Sloth 도입**                     | 65 rule 수기 → SLO YAML 자동 생성                                           | Phase 3-B 위 |
| **vbgw fuzz job**                  | 옛 ci.yml 에 있던 capacity fuzz, weekly cron                                | follow-up  |

내 추천: **머지 → cutover 실행 → prod cutover + DR** 순.

## 7. 작업 시작 checklist

```
□ 이 HANDOFF.md 끝까지 읽기
□ docs/reference/slo.md — SLO 임계 머리에 넣기
□ TaskList 로 현재 task 확인
□ git status / git log -10
□ AskUserQuestion 으로 §6 표 제시
```

## 8. 자주 쓰는 명령

```bash
# Helm 차트 검증
cd deploy/helm && ENV=staging make lint

# Prometheus rules
promtool check rules deploy/k8s-bootstrap/manifests/prometheus-rules/*.yaml

# GHA YAML parse
python3 -c 'import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob(".github/**/*.yml", recursive=True)]'

# 백엔드 lint/type
cd services/backend && ruff check app/ && mypy app/ --ignore-missing-imports

# Go build all
for m in services/vbgw-ai services/vbgw-bridge services/vbgw-orchestrator; do (cd "$m" && go build ./...) || break; done

# Dev integration
./scripts/integration/dev-integration.sh up
```

## 9. 의도적 제외

- WAFv2 규칙 자체 (Phase P-A 변수만 — 규칙은 별도) / multi-region 활성화 (`atlas_dr_region_enabled=false` default) / Velero S3 cross-region replication / Tracing OTel / Image signing cosign / Load test k6 (Gate A 자동화) / Chaos-mesh (Gate B) / vbgw fuzz job / 실 cutover 실행 (Stage A부터 사람 의사결정).
- Phase P-A 의 prod terraform 은 작성 완료 — `terraform apply` 는 사람이 실행 (AWS credential / Atlas key 필요).

## 10. 참조

- 비즈니스 문서: `docs/business/`
- Variables/Secrets: `docs/guide/ci-cd.md` §4
- Slack: `#ops-incident`, `#ops-alerts`, `#ops-platform`, `#ops-deploy`
- Monorepo 마이그레이션 plan: `docs/guide/monorepo-migration-plan.md`
- 옛 cross-project (history): `docs/guide/cross-project-integration.md`

## 11. monorepo 통합 직후 — 사용자 host 에서 할 일

```bash
# 1) push (sandbox 는 auth 못함)
cd ~/AgenticOE_v2
git push -u origin feat/monorepo-merge backup/pre-monorepo
git push origin v0-pre-monorepo

# 2) PR open → 리뷰 + CI green → merge commit (squash/rebase 금지 — subtree history 보존)

# 3) merge 후 vbgw_v2 archive
cd ~/vbgw_v2 && rm -f .git/index.lock
# GitHub UI → Settings → Archive (이력 보존, 새 PR/issue 차단)

# 4) (선택) repo rename: AgentOE → agentoe
#    git remote set-url origin git@github.com:kchul199/agentoe.git
```

---

> **수정 규칙**: 이 문서는 phase 끝마다 갱신. §3 / §6 / §9 가 가장 자주 바뀜. §1 (절대 규칙) 은 CLAUDE.md 변경 시에만.
