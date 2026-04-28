# HANDOFF — agentoe monorepo 세션 인계 문서

> **이 문서를 첫 5분 안에 다 읽어라.** 새 세션이 작업을 이어받을 때 필요한 모든 컨텍스트가 여기 있다.
> 마지막 업데이트: 2026-04-28 · Phase M (monorepo 통합) 완료 시점.

## 0. TL;DR

- 프로젝트: **agentoe** — 멀티 테넌트 Agentic Callbot. **단일 monorepo** (옛 AgenticOE_v2 + vbgw_v2 통합 완료).
- 단일 진실 소스 4개:
  - **`CLAUDE.md`** (프로젝트 규칙 3가지) — 절대 어기지 말 것.
  - **이 `HANDOFF.md`** — 현재 상태 / 다음 액션.
  - **`docs/reference/slo.md`** — SLO 임계치.
  - **`contracts/proto/voicebot.proto`** — vbgw bridge ↔ AgentOE backend gRPC contract (canonical).
- 코드 루트: `services/` (backend, frontend, vbgw-ai, vbgw-bridge, vbgw-orchestrator, freeswitch).
- **현재 상태: Phase 1-3 + X/Y/Z/D + M 모두 완료.** vbgw cutover 실행 + 운영 배포 단계만 남음. 다음 후보 §6.

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
| **푸시 + PR open + main 머지**     | feat/monorepo-merge push → PR → CI green → merge commit (squash 금지)      | 사용자 host |
| **vbgw-ai → backend cutover 실행** | `docs/runbook/vbgw-ai-cutover.md` 의 4 stage. dev smoke 3회 OK 후 진행      | M 머지 후  |
| **Production cutover + DR**        | environments/prod terraform stack, Velero PV 백업, region failover         | Phase 1 ✓  |
| **Security hardening**             | Kyverno, Falco, cosign, Linkerd mTLS                                       | 인프라 안정 |
| **Load testing + Chaos**           | k6 + chaos-mesh                                                            | Phase 3 ✓  |
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

- WAFv2 규칙 자체 / multi-region / 백업 자동화 (Velero) / Tracing OTel / Image signing cosign / Load test k6 / vbgw fuzz job / 실 cutover 실행

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
