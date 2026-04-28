# HANDOFF — AgentOE 프로젝트 세션 인계 문서

> **이 문서를 첫 5분 안에 다 읽어라.** 새 세션이 작업을 이어받을 때 필요한 모든 컨텍스트가 여기 있다.
> 마지막 업데이트: 2026-04-26 · Phase X (cross-project 통합) 완료 시점.

## 0. TL;DR

- 프로젝트: **AgentOE** — 멀티 테넌트 Agentic Callbot. backend(FastAPI) + frontend(React SPA), AWS EKS 배포.
- **vbgw 는 별도 프로젝트** (``, Go + FreeSwitch). proto contract 만 우리가 owner. 자세히 §11.
- 단일 진실 소스 5개:
  - **CLAUDE.md** (프로젝트 규칙 3가지) — 절대 어기지 말 것.
  - **이 HANDOFF.md** — 현재 상태 / 다음 액션.
  - **`docs/reference/slo.md`** — SLO 임계치.
  - **`contracts/proto/voicebot.proto`** — vbgw 와의 gRPC contract (canonical).
  - **TaskList** — 작업 단위 추적 (`TaskList` 도구로 본다).
- 코드 루트: `` (이상하게 들리지만 history 적으로 이름이 굳음).
- **현재 상태: Phase 1-3 + cross-project 통합 셋업 완료.** 다음 후보는 §6.

## 1. 절대 규칙 (CLAUDE.md)

1. **Performance First** — 모든 I/O (STT, LLM, DB) 는 async/await, non-blocking.
2. **Latency is King** — 실시간 콜봇. 불필요한 루프/지연 금지.
3. **Error Handling** — 도구 호출(Tool Calling) 실패 시 통화 끊지 마라 — 폴백 시나리오 필수.

위 셋 중 하나라도 위반하는 구현은 머지 금지.

## 2. 디렉토리 지도

```
AgenticOE_v2/
├── CLAUDE.md                    # 프로젝트 규칙 (위 §1)
├── docs/                        # ★ 비즈니스 문서 (한국어 docx/xlsx). 코드와 별개.
└──                     # ★ 모든 코드/배포 자산 (이름 history)
    ├── README.md
    ├── Makefile, docker-compose.{dev,}.yml
    ├── backend/                 # FastAPI + Mongo + Redis
    │   ├── app/
    │   │   ├── main.py          # 미들웨어 체인: HTTPMetrics → Logging → KillSwitch → RateLimit → Idempotency → Admission
    │   │   ├── core/metrics.py  # 메트릭 중앙 레지스트리 (in-process + prometheus_client 이중기록)
    │   │   ├── middleware/http_metrics_middleware.py   # SLO용 http_* 시리즈 (Phase 3-E)
    │   │   ├── middleware/{logging,admission,idempotency,kill_switch,rate_limit}_middleware.py
    │   │   └── api/v1/routers/metrics.py
    │   ├── Dockerfile           # 멀티 스테이지 prod (UID 10001, tini)
    │   ├── Dockerfile.dev       # docker-compose 용 dev
    │   └── pyproject.toml
    ├── vbgw/                    # 음성 브리지 (Phase 2-G 스캐폴드)
    │   ├── app/main.py          # gRPC + aiohttp WS + prometheus + 4 ports
    │   ├── Dockerfile
    │   └── pyproject.toml
    ├── frontend/                # React SPA (Vite)
    │   ├── src/
    │   ├── Dockerfile           # nginx-unprivileged + envsubst entrypoint
    │   ├── nginx.conf
    │   ├── docker-entrypoint.sh
    │   └── package.json
    ├── deploy/
    │   ├── terraform/           # AWS infra (Phase 1)
    │   │   ├── bootstrap-state/             # S3 + DynamoDB lock
    │   │   ├── modules/
    │   │   │   ├── vpc/                     # 3 AZ, NAT, flow logs, endpoints
    │   │   │   ├── eks/                     # cluster + system NG + addons
    │   │   │   ├── ecr/                     # IMMUTABLE repos
    │   │   │   ├── elasticache/             # Redis 7.1, multi-AZ, TLS, AUTH
    │   │   │   ├── atlas/                   # MongoDB Atlas, PIT enabled
    │   │   │   ├── secrets/                 # Secrets Manager + KMS
    │   │   │   ├── alb/                     # ACM cert
    │   │   │   ├── bootstrap/               # ALB ctrl / external-dns / backend SA IRSA
    │   │   │   └── github-oidc/             # ★ GHA → AWS keyless (Phase 2-F)
    │   │   └── environments/staging/        # composition + tfvars
    │   ├── k8s-bootstrap/       # ALB ctrl / cert-manager / ESO / Karpenter / kps
    │   │   ├── Makefile, README.md
    │   │   ├── values/{alb-controller, cert-manager, external-secrets, ingress-nginx, karpenter, kube-prometheus-stack, alertmanager}.values.yaml
    │   │   └── manifests/{cluster-issuer-letsencrypt, cluster-secret-store-aws, alertmanager-receivers-externalsecret}.yaml
    │   │       └── prometheus-rules/{slo-recording, slo-alerting, infra-alerting}.yaml   # Phase 3-B
    │   ├── helm/                # 3개 차트 (Phase 1-F/G/H)
    │   │   ├── agentoe-backend/{Chart.yaml, values.yaml, templates/...}
    │   │   ├── agentoe-vbgw/
    │   │   ├── agentoe-frontend/
    │   │   ├── values/{staging,prod,shared}/   # 환경별 override
    │   │   ├── scripts/render-values.sh        # tf output → REPLACE_* 치환
    │   │   ├── Makefile
    │   │   └── README.md
    │   └── observability/dashboards/   # Grafana JSON + sidecar kustomization (Phase 3-D)
    ├── docs/                    # 코드 옆 운영 문서
    │   ├── HANDOFF.md           # ← 이 파일
    │   ├── adr/                 # 아키텍처 결정
    │   ├── guide/               # ci-cd, observability, ...
    │   ├── reference/           # slo.md (★), 데이터 모델 등
    │   ├── runbook/             # alert-response-*, staging-bringup, redis-outage, ...
    │   └── guide/cross-project-integration.md   # ★ vbgw_v2 와의 통합 (Phase X)
    ├── contracts/               # ★ canonical proto (Phase X)
    │   ├── proto/voicebot.proto # vbgw 와의 gRPC 계약 — owner
    │   ├── gen/{python,go}/     # 생성된 stub
    │   ├── scripts/{sync-to-vbgw,verify-vbgw-proto}.sh
    │   └── Makefile
    ├── backend/app/grpc_server/ # ★ VoicebotAiService 구현 (Phase Y)
    │   ├── server.py            # FastAPI lifespan 통합 + grpc.aio.server
    │   ├── voicebot_service.py  # Servicer (StreamSession bidi)
    │   └── metrics.py           # gRPC 메트릭 + SLO 시리즈 백업
    ├── backend/app/grpc_stubs/  # ★ vendored proto stub (Phase Y-B)
    ├── vbgw/                    # ★ TEST STUB ONLY (실 배포 안 함). vbgw_v2 가 실 구현
    └── .github/
        ├── actions/             # composite actions (Phase 2-A)
        │   ├── setup-aws-oidc/, setup-helm/, kubeconfig/, ecr-build-push/, helm-deploy/
        └── workflows/
            ├── ci.yml                # 백엔드 lint/test/coverage
            ├── validate.yml          # PR 게이트 (helm lint, tf fmt, Trivy fs, hadolint, frontend lint)
            ├── build-images.yml      # main/tag → ECR push (matrix backend/vbgw/frontend)
            ├── deploy-staging.yml    # workflow_run chain → helm upgrade --atomic
            └── deploy-production.yml # tag v* → plan → manual approve → canary 10% → promote
```

## 3. 무엇이 이미 만들어졌나 (Phase 1-3 요약)

### Phase 1 — 인프라 + Helm + 스테이징 브링업 (#29 ~ #37, 모두 완료)
- Terraform: VPC / EKS 1.29 / ECR(IMMUTABLE) / ElastiCache Redis / MongoDB Atlas / Secrets+KMS / ALB+ACM / IRSA roles. 33개 .tf, 모두 hcl2 파서 통과.
- k8s-bootstrap: kube-prometheus-stack, ALB controller, cert-manager (LE DNS01), ESO, ingress-nginx (NLB+PROXY), Karpenter — 모두 helm chart 버전 핀.
- Helm 차트 3개 (backend/vbgw/frontend): topology spread, PDB, HPA, preStop drain, terminationGracePeriodSeconds, readOnlyRootFilesystem, IRSA, ESO 시크릿 → ConfigMap+Secret envFrom, REDIS_URL/MONGODB_URI 컨테이너 합성, ServiceMonitor.
- 환경 values: staging (작은 규모, agentic 100% canary) / prod (priorityClass critical, WAFv2, S3 access logs, agentic 10% canary).
- staging-bringup runbook (`docs/runbook/staging-bringup.md`) — 0→running 약 1h50m.

### Phase 2 — CI/CD (#38 ~ #45)
- 5개 composite action: `setup-aws-oidc` (OIDC AssumeRole), `setup-helm`, `kubeconfig`, `ecr-build-push` (Trivy 게이트 + SARIF), `helm-deploy` (render-values → diff → atomic upgrade → smoke → auto-rollback).
- 5개 워크플로: ci, validate, build-images, deploy-staging (workflow_run chain), deploy-production (plan → approval → canary 10% bake → promote).
- `terraform/modules/github-oidc` — `ecr_push` + `eks_deploy` + 옵션 `tf_plan` IAM role. trust policy 의 `sub` 패턴이 GitHub Environment 게이트와 연동.
- prod-grade Dockerfile 3개 (UID 10001/101 non-root, tini PID-1).
- `docs/guide/ci-cd.md` — 워크플로 매트릭스, branch protection 권장, Variables/Secrets 표.

### Phase 3 — Observability (#46 ~ #51)
- `docs/reference/slo.md` — 7개 SLO (api success/latency, agentic success/latency, vbgw setup/drop, jwks). multi-window multi-burn-rate 임계 표 + error budget freeze policy.
- `backend/app/middleware/http_metrics_middleware.py` — `http_requests_total{method,route,status}` + `http_request_duration_seconds` + in-flight gauge. 라벨 카디널리티 보호 (route template 만 사용).
- `vbgw/app/main.py` — `agentoe_call_setup_total{result}`, `agentoe_call_terminations_total{reason}`, `agentoe_call_duration_seconds`.
- 3개 PrometheusRule 파일 / 65개 rule. 8개 그룹 recording (5m/30m/1h/6h/1d/3d/30d 윈도우 + budget remaining).
- Alertmanager values: page → PagerDuty + #ops-incident, ticket → #ops-alerts (KST 야간 mute), inhibit rules.
- 4개 Grafana 대시보드 (api-slo/agentic/vbgw/infra) — sidecar 자동 import.
- `docs/guide/observability.md` + 3개 alert-response runbook.

## 4. 전체 작업 통계 (참고)

- 총 task: 52개 (모두 closed except #52 자체).
- 코드/매니페스트 파일: 약 130개 ( 안, node_modules 제외).
- Dockerfile 3개, Helm 차트 3개, GHA 워크플로 5개 + composite 5개, Terraform 모듈 9개.
- Grafana 대시보드 4개 (총 39 패널), PrometheusRule 65개.

## 5. 알아야 할 함정 / 결정사항 (코드만 보면 모름)

> **이 절은 다음 세션이 같은 실수를 안 하도록 한다.** 코드에 주석 없이 굳어진 결정만 적는다.

### 5.1 라벨 카디널리티
- 모든 Prometheus 시리즈는 **유한한 알려진 집합** 의 라벨만 사용. tenant_id 처럼 무한 가능한 라벨도 결국 자연 상한이 있어서 허용했지만, 임의 path/user_agent 라벨은 절대 금지.
- `http_metrics_middleware.py` 가 route 라벨을 FastAPI path template (`/api/v1/sessions/{id}`) 로만 박는 이유 — 미매칭은 `UNKNOWN` 으로 흡수.

### 5.2 Helm chart 의 시크릿 합성 패턴
- 시크릿 (REDIS_AUTH_TOKEN, MONGO_APP_PASSWORD) 은 ESO 가 동기화한 K8s Secret 에 들어 있고, ConfigMap 의 endpoint 와 결합해 컨테이너 env 에서 `REDIS_URL=rediss://default:$(REDIS_AUTH_TOKEN)@$(REDIS_HOST):$(REDIS_PORT)/0` 로 합성.
- `$(VAR)` 치환은 K8s 가 envFrom 으로 주입한 변수에만 작동. 값 자체에 `$()` 가 박히면 보안 사고 — schema 가 secret-only 이므로 안전하지만 **새 시크릿 추가 시 항상 envFrom 경로로**.

### 5.3 ECR IMMUTABLE
- ECR repository 는 `image_tag_mutability=IMMUTABLE`. 같은 sha 태그 재푸시 = 빌드 실패 = 정상 동작.
- 재시도 시 항상 새 commit / 새 sha. `--amend` 해도 sha 가 바뀜.

### 5.4 GitHub OIDC trust 의 sub 패턴
- `ecr_push` role 의 sub = `refs/heads/main` 또는 `refs/tags/v*`. PR 에서는 절대 ECR 에 못 push (의도).
- `eks_deploy` role 의 sub = `:environment:staging|production`. **GitHub Environment 의 required reviewers 가 게이트** 역할 — AWS 측에서 강제됨.

### 5.5 helm-deploy 의 atomic 모드
- `--atomic` = 실패 시 자동 rollback. 우리는 그것 외에 추가로 smoke 단계에서 livez 응답 확인 후 실패 시 명시적 `helm rollback` 도 실행 (이중 안전망).
- canary release 는 `agentoe-backend-canary` 별도 release 로 띄우고, ALB action.weighted-routing 어노테이션으로 10% 트래픽. cleanup-canary job 이 promote 후 uninstall.

### 5.6 SLO 윈도우 — 30d retention 짧아도 OK
- kps 기본 retention 7d 인데 SLO 는 30d. **recording rule 이 30d 비율을 미리 합산**하므로 raw retention 이 짧아도 budget tracking 가능. raw retention 늘릴 필요 없음.

### 5.7 vbgw 는 placeholder 다
- `vbgw/app/main.py` 는 SLO/Helm chart 가 정상 동작하도록 한 **최소 진입점**. 실제 SIP/RTP/codec 로직은 후속 PR. WS 핸들러는 echo 카운터만.

### 5.8 백엔드 미들웨어 순서
- `main.py` 의 `add_middleware` 는 Starlette 규약상 **역순 실행**. 마지막에 추가한 게 가장 outer.
- 현재 outer-most = `HTTPMetricsMiddleware` — 모든 inner 미들웨어 비용을 latency 에 포함시키는 게 맞음 (고객 perceived latency).

### 5.9 Atlas NAT EIP allowlist
- Atlas 는 IP allowlist 기반. NAT EIP 가 바뀌면 끊김.
- staging main.tf 의 `local.nat_cidrs = [for ip in module.vpc.nat_public_ips : "${ip}/32"]` 가 자동 feed.
- terraform apply 가 NAT 재생성하면 다음 apply 까지 connection 끊김 — staging 만 single NAT 라 더 위험.

### 5.10 cert-manager DNS01 IRSA
- ACME challenge 가 Route53 TXT 레코드를 만든다. cert-manager 의 SA 가 Route53 ChangeResourceRecordSets 권한 가진 IRSA 필요.
- bootstrap module 의 `external_dns_role_arn` 이 같은 권한 — 둘이 공유해도 OK.

## 6. 다음 단계 후보 (선택지)

ANY 한 개 골라서 진행. AskUserQuestion 으로 사용자에게 물어볼 것.

| 후보                                  | 무엇                                                                                             | 의존성        |
|---------------------------------------|--------------------------------------------------------------------------------------------------|---------------|
| **monorepo 통합 (vbgw_v2 ↔ AgenticOE_v2)**  | 두 repo 를 단일 `agentoe` 로 합침. cross-project sync / cross-repo PR 폐지. **계획서**: `docs/guide/monorepo-migration-plan.md` (사용자 5개 결정 + GO 신호 후 실행). 통합 후 cutover 가 단일 PR 로 안전. | 없음 (계획 단계) |
| **vbgw-ai → backend cutover 실행**     | Phase Z 로 vbgw chart 에 canary 블록 추가됨. **dev 통합 테스트 (Phase D) 3회 OK 후** staging 100% → prod 10% (24h) → 50% (12h) → 100% promote. monorepo 통합 후 단일 PR 권장. | Phase Y/Z/D ✓ — runbook: `docs/runbook/vbgw-ai-cutover.md`, dev 검증: `docs/runbook/dev-integration-test.md` |
| **Production cutover + DR**           | environments/prod terraform stack, Velero PV 백업, Atlas PIT 검증, region failover runbook, RTO/RPO | Phase 1 완료  |
| **Security hardening**                | Kyverno admission policy, Falco runtime, cosign 이미지 서명+verify, Linkerd mTLS, secret rotation | Phase 1 완료  |
| **Load testing + Chaos**              | k6 시나리오 (REST/WS/agentic) + GHA runner, chaos-mesh 실험, 결과 대시보드                          | Phase 3 완료  |
| **vbgw 메트릭 정합성 audit**           | vbgw_v2 의 prometheus exporter 가 우리 SLO doc 의 시리즈 (`agentoe_call_setup_total` 등) 노출하는지 확인. 안 되면 vbgw_v2 측 PR | Phase 3 완료 |
| **Tracing (Tempo/Jaeger)**            | trace_id 가 이미 로그에 있음. exporter + propagation 추가하면 분산 트레이스 (Phase X 의 SIP Call-ID 까지)         | Phase 3 권장  |
| **Loki + structured log queries**     | Grafana 에서 알람 ↔ 로그 한 화면 — 인시던트 대응 시간 단축                                          | Phase 3 권장  |
| **Sloth 도입**                        | SLO YAML → PrometheusRule 자동 생성. 현재 65 rule 수기 — 변경 시 일관성 위험                       | Phase 3-B 위 |
| **canary 게이트 PromQL 화**          | deploy-production canary bake 가 현재 로그 grep. Prometheus 쿼리로 진화                            | Phase 3 완료  |

내 추천 우선순위: **AgentOE backend 가 VoicebotAiService 구현** > **vbgw 메트릭 정합성 audit** > **Production cutover + DR** > 나머지. 이유:
- 첫 번째가 cross-project 통합의 진짜 endgame — 이게 되어야 우리 agentic 로직이 실제 통화에 적용됨.
- 두 번째는 SLO 가 진짜 측정되는지 안 되는지 갈리는 베이스라인. 빠르게 확인 가능.
- prod cutover 는 staging 검증 후 자연스러운 다음.

## 7. 작업 시작 checklist (새 세션 첫 5분)

```
□ 이 문서 (HANDOFF.md) 처음부터 끝까지 읽기
□ docs/reference/slo.md — SLO 임계값 머리에 넣기
□ TaskList 도구로 현재 task 상태 확인
□ git status / git log -10 — 최근 변경
□ 사용자에게 어디서 이어갈지 AskUserQuestion (§6 표 기반)
```

## 8. 자주 쓰는 명령

```bash
# Helm 차트 검증 (3 차트 × 2 env)
cd deploy/helm && ENV=staging make lint

# Terraform 모듈 syntax 빠른 점검 (실제 plan 은 AWS creds 필요)
python3 -c 'import hcl2; [hcl2.load(open(f)) for f in __import__("glob").glob("deploy/terraform/**/*.tf", recursive=True)]'

# Prometheus rule 문법 — promtool
promtool check rules deploy/k8s-bootstrap/manifests/prometheus-rules/*.yaml

# GHA 워크플로 YAML 점검
python3 -c 'import yaml,glob; [yaml.safe_load(open(f)) for f in glob.glob(".github/**/*.yml", recursive=True)]'

# 백엔드 빠른 sanity (의존성 설치 후)
cd backend && ruff check app/ && mypy app/ --ignore-missing-imports
```

## 9. 의도적 제외 (우리가 안 한 것 — 다음 사람이 헷갈리지 말 것)

- **API 게이트웨이/WAF 자체** — ALB + WAFv2 ARN 어노테이션 자리는 prod values 에 있지만 WAF 규칙 자체는 미작성.
- **multi-region** — 단일 region (ap-northeast-2). region failover 는 Phase 4 후보.
- **백업 자동화** — Atlas PIT 만 enable. Velero / S3 동기화 미구현.
- **Tracing** — trace_id 만 로그에 박혀 있음. OTel exporter 미설정.
- **Image signing** — Trivy 스캔까지. cosign 서명/검증 없음.
- **vbgw 실 음성 처리** — 이 repo 안에는 안 함. 별도 프로젝트 `` 책임 (§11 참고).
- **load test** — k6 / chaos mesh 미작성.
- **vbgw-ai → backend cutover** — backend 가 VoicebotAiService 구현 (Phase Y) 했지만 vbgw_v2 의 bridge 가 아직 vbgw-ai 호출 중. cutover PR 별도.

## 10. 참조 — 외부 문서

- 비즈니스/기획 문서: `docs/` (한국어 docx). 코드 의사결정은 여기 적힌 것이 우선.
- AWS account / Atlas org id 등: `terraform/environments/staging/terraform.tfvars` (gitignore 됨, .example 만 커밋).
- GitHub repo Variables/Secrets 목록: `docs/guide/ci-cd.md` §4.
- Slack 채널: `#ops-incident`, `#ops-alerts`, `#ops-platform`, `#ops-deploy`.

## 11. Cross-project — vbgw_v2 와의 관계

**자세한 가이드**: `docs/guide/cross-project-integration.md`

### 핵심 포인트

| 항목                | AgenticOE_v2                          | vbgw_v2                                       |
|---------------------|----------------------------------------|----------------------------------------------|
| 위치 (host)         | `~/AgenticOE_v2`                       | `~/AgenticOE_v2`                                   |
| 위치 (이 세션 VM)    | `mnt/AgenticOE_v2`                     | `mnt/vbgw_v2`                                 |
| 언어                | Python (FastAPI) + React               | Go + FreeSwitch                               |
| 책임                | Agentic 오케스트레이션 / 인프라 / proto owner | SIP/RTP / 코덱 / WS↔gRPC bridge             |
| Helm chart          | `agentoe-{backend,frontend}` (vbgw 는 deprecated) | `deploy/helm/vbgw/` (3 deployment) |
| Proto               | **owner** (`contracts/proto/voicebot.proto`)    | consumer (3 곳 sync)                          |

### 실 구조 vs vbgw_v2 의 CLAUDE.md
**CLAUDE.md 는 stale 함**: C++/PJSIP 라고 적혀 있지만 실제 코드는 Go + FreeSwitch (`vbgw-ai/`, `vbgw-freeswitch/`). `legacy/` 디렉토리에만 옛 C++ 흔적. vbgw_v2 측 CLAUDE.md 갱신 PR 이 별도로 필요. **새 세션이 vbgw_v2 만 mount 한 채 시작하면 잘못된 컨텍스트 받을 위험 있음.**

### 우리 placeholder 의 위상
- `vbgw/` — Python placeholder. **integration test stub 전용, 실 배포 안 함.**
- `deploy/helm/agentoe-vbgw/` — DEPRECATED. vbgw_v2 자체 chart 사용.
- 둘 다 `README.md` / `DEPRECATED.md` 로 명시.

### Proto 변경 워크플로
```bash
# AgenticOE_v2 측
$EDITOR contracts/proto/voicebot.proto
cd contracts && make gen-python && make gen-go
make sync-vbgw VBGW=$HOME/vbgw_v2     # vbgw_v2 의 3 곳 동기화
make verify-vbgw VBGW=$HOME/vbgw_v2   # drift 검증

# 양쪽 PR
#   1) AgenticOE_v2 PR: proto 변경 + gen/* 갱신
#   2) vbgw_v2 PR: 동기화된 proto + 재생성된 *.pb.go
```

### 알려진 정합성 이슈 (cross-project-integration.md §8)
- ✅ ~~vbgw_v2 의 CLAUDE.md stale~~ — Phase Z-D 에서 갱신 (Go + FreeSwitch 명시)
- vbgw_v2 의 metric exporter 가 우리 SLO doc 시리즈 노출 여부 미검증 — Phase Y 에서 backend 가 백업 발화원으로 동일 시리즈 노출. vbgw 측 audit 별도.
- ECR namespace 분리 — 통합 검토 필요
- ✅ ~~AgentOE backend 의 VoicebotAiService 미구현~~ — Phase Y 완료. cutover 준비 OK.
- ✅ ~~vbgw chart 의 `GRPC_AI_ADDR` env key 버그~~ — Phase Z-B 에서 `AI_GRPC_ADDR` 로 수정 (Go config 와 정합)
- vbgw chart 에 canary block 추가됨 (Phase Z-B). 실제 cutover 실행은 별도 PR / 운영 결정.

---

> **수정 규칙**: 이 문서는 작업이 phase 단위로 끝날 때마다 갱신한다. §3 (현황), §6 (다음 후보), §9 (의도적 제외), §11 (cross-project) 가 가장 자주 바뀐다. §1 (절대 규칙) 은 CLAUDE.md 변경 시에만.
