# Plan — AgenticOE_v2 + vbgw_v2 → 단일 monorepo 통합 계획

> 상태: **계획 단계** — 사용자 승인 후 Phase M 으로 실행.
> 마지막 갱신: 2026-04-27

## 0. TL;DR

- 두 git repo 를 **하나의 monorepo `agentoe`** 로 합친다.
- 합치는 이유 (§1), 목표 구조 (§2), 마이그레이션 5 phase (§3), 영향 (§4-§6), 리스크 (§7), 일정 (§8).
- 권장 시점: 운영 cutover (vbgw-ai → backend) **전**. 통합 후 cutover 가 단일 PR 로 가능 → 안전함.
- 비권장 시점: cutover 도중. 두 변수 동시에 흔들림.

## 1. 왜 합치나 / 왜 안 합치나

### 합치는 이점
| 영역                        | 현재 (분리)                                      | 통합 후                                           |
|-----------------------------|--------------------------------------------------|---------------------------------------------------|
| Proto contract 변경         | AgenticOE_v2 PR + sync 스크립트 + vbgw_v2 PR (2 PR) | 단일 PR — gen 결과 모두 같이 머지                |
| cross-cutting refactor      | 두 PR 머지 순서 의존 (배포 순서까지 신경)         | 단일 atomic 변경                                 |
| CI/CD                       | 두 워크플로 + cross-trigger (workflow_run)        | 단일 워크플로, path-filter 로 영향 받는 서비스만   |
| Dev 환경                    | 두 docker-compose + 외부 network override 필요    | 단일 compose, 그냥 `up`                          |
| Helm chart                  | 두 chart (`agentoe-vbgw` deprecate, `vbgw_v2/charts/vbgw`) | 단일 chart 또는 명확한 분리, drift 위험 ↓        |
| Doc                         | HANDOFF.md / cross-project-integration.md / vbgw CLAUDE.md 셋이 동기 필요 | 단일 진실 소스                                   |
| 컨벤션                      | Python (AgenticOE) vs Go (vbgw) 일관성 약함       | 같은 디렉토리에서 같은 lint/CI/PR template       |
| 의존성 / 보안 패치          | 두 repo 의 dependabot                             | 한 repo, 한 dashboard                            |

### 합치는 비용 / 단점
- **git history 보존 복잡** — `git subtree` 또는 `git filter-repo` 필요. 잘못 하면 history 손실.
- **단일 repo 사이즈 증가** — vbgw_v2 만 3,419 git-tracked + 우리 ~378 = 약 4,000 file. clone 시간 ↑ (그래도 작은 편).
- **CI 시간 잠재 증가** — path-filter 안 쓰면 모든 PR 이 모든 lint/test 돌림.
- **권한 분리 어려움** — 외부 contractor 가 vbgw 만 보게 못 함 (현재도 같은 사람이 owner 라 무관).
- **branch 정책 단순화 필요** — 양쪽이 다른 branch 패턴이면 통일.

### 결론
- charls 가 **두 프로젝트 모두 owner** + **cross-cutting 변경 빈도 상승 중** (Phase X/Y/Z 가 모두 cross-project) → 합치는 게 합리.
- 단점은 거의 없음. git history 보존만 신경.

## 2. 목표 구조

```
agentoe/                          # 새 monorepo 루트 (이름은 결정 필요 — §A 참고)
├── README.md                     # 프로젝트 소개
├── CLAUDE.md                     # 절대 규칙 + HANDOFF 가리킴
├── HANDOFF.md                    # 캐노니컬 (현재 docs/HANDOFF.md → 루트로 승격)
├── CHANGELOG.md
├── .gitignore                    # 통합
├── Makefile                      # 통합 dev workflow
│
├── docs/
│   ├── business/                 # ★ 옛 AgenticOE_v2/docs/*.docx, *.xlsx (한국어 기획)
│   ├── adr/                      # 아키텍처 결정
│   ├── guide/                    # ci-cd, observability, monorepo-migration-plan, ...
│   ├── reference/                # slo.md, 데이터 모델
│   ├── runbook/                  # alert-response-*, staging-bringup, vbgw-ai-cutover, ...
│   ├── performance/              # ★ 옛 vbgw_v2/docs/performance/sla_baseline
│   └── HANDOFF.md → ../HANDOFF.md   # symlink
│
├── contracts/                    # 단일 진실 소스 — sync 스크립트 폐지 (한 repo 내 import)
│   ├── proto/voicebot.proto
│   ├── gen/{python,go}/          # CI 가 자동 생성 + 검증 (수기 sync 폐지)
│   └── Makefile
│
├── services/                     # 모든 실 서비스 한 곳
│   ├── backend/                  # Python FastAPI  (옛 skeleton/backend)
│   ├── frontend/                 # React SPA      (옛 skeleton/frontend)
│   ├── vbgw-ai/                  # Go AI engine   (옛 vbgw_v2/vbgw-ai) — cutover 후 deprecate
│   ├── vbgw-bridge/              # Go             (옛 vbgw_v2/vbgw-freeswitch/bridge)
│   ├── vbgw-orchestrator/        # Go             (옛 vbgw_v2/vbgw-freeswitch/orchestrator)
│   └── freeswitch/               # FS Dockerfile + dialplan (옛 vbgw_v2/vbgw-freeswitch/{config,Dockerfile.freeswitch})
│
├── deploy/
│   ├── terraform/                # 옛 그대로 (skeleton/deploy/terraform)
│   ├── k8s-bootstrap/            # 옛 그대로
│   ├── helm/
│   │   ├── agentoe-backend/
│   │   ├── agentoe-frontend/
│   │   └── vbgw/                 # ★ 옛 vbgw_v2/charts/vbgw 통합 (단일 chart)
│   │       └── (skeleton/deploy/helm/agentoe-vbgw 는 삭제 — DEPRECATED 였음)
│   └── observability/dashboards/ # Grafana JSON
│
├── docker/                       # 통합 docker-compose
│   ├── compose.yml               # 옛 AgenticOE_v2 docker-compose.yml + vbgw 추가
│   ├── compose.dev.yml           # 옛 docker-compose.dev.yml + vbgw 옵션
│   └── compose.integration.yml   # 통합 테스트 (단일 파일 — override 분리 불필요)
│
├── scripts/
│   ├── integration/              # 옛 그대로 (smoke client, dev-integration.sh)
│   ├── sync-vbgw.sh              # 폐지 — 단일 repo 라 sync 불필요
│   └── ...
│
├── .github/
│   ├── workflows/
│   │   ├── ci.yml                # 통합 — path-filter 로 backend/vbgw/frontend 분기
│   │   ├── validate.yml
│   │   ├── build-images.yml      # matrix: backend/vbgw-ai/vbgw-bridge/vbgw-orchestrator/frontend
│   │   ├── deploy-staging.yml
│   │   └── deploy-production.yml
│   └── actions/                  # composite actions 그대로
│
├── tools/                        # buf, lefthook, dev tools (선택)
│
└── legacy/                       # 옛 vbgw_v2/legacy (C++ PJSIP — 참조용, 빌드 안 함)
```

### 결정 필요 사항
- **A) repo 이름**: `agentoe` (간결) / `agentoe-platform` (의도 명확) / 기존 `AgenticOE_v2` 유지?
- **B) `skeleton/` 디렉토리 유지 여부**: 현재 AgenticOE_v2 는 모든 게 `skeleton/` 안. 통합 시 (1) 그대로 유지, (2) 폐지하고 루트로 끌어올림. 권장: 폐지 (history 적인 이름이지만 monorepo 전환은 이름 정리 적기).
- **C) Helm chart `agentoe-vbgw` 처리**: DEPRECATED 표기만 했음. 통합 시 (1) 삭제, (2) 그대로 유지. 권장: 삭제 (`vbgw_v2/charts/vbgw` 가 canonical).
- **D) 새 repo / 기존 repo 이어쓰기**: (1) 새 GH repo 생성, (2) AgenticOE_v2 에 vbgw_v2 import. 권장: (2) — AgenticOE_v2 에 vbgw_v2 history 보존하며 import (subtree).

## 3. 마이그레이션 단계 (5 phase)

### Phase M-1: 사전 정리 (양 repo 에서 각자 — merge 전)
1. AgenticOE_v2 의 모든 PR 머지 / 닫기. main 만 남게.
2. vbgw_v2 의 모든 PR 머지 / 닫기. main 만 남게.
3. 양쪽 CI 가 main 에서 green.
4. 백업 — `git clone --mirror` 로 양 repo 의 bare copy 보관 (롤백 시).
5. **현재 진행 중인 cutover (vbgw-ai → backend) 는 통합 후 진행.** 통합 전엔 staging 도 진행 X.

### Phase M-2: 통합 PR (subtree merge, history 보존)
다음을 단일 PR (단, 머지는 신중) 또는 일련의 작은 PR 로:

```bash
# 0) AgenticOE_v2 를 작업 base 로 사용
cd ~/AgenticOE_v2
git checkout -b feat/monorepo-merge

# 1) vbgw_v2 를 subtree 로 import (history 보존)
git remote add vbgw https://github.com/kchul199/vbgw_v2.git
git fetch vbgw main
git subtree add --prefix=_imported/vbgw_v2 vbgw/main --squash=false

# 2) skeleton/ 폐지 — 루트로 끌어올림 (file move + git mv)
git mv skeleton/* .
rmdir skeleton

# 3) 디렉토리 재구조화 (목표 §2 와 일치하게)
mkdir -p services docker
git mv backend services/
git mv frontend services/
git mv vbgw services/vbgw-test-stub      # placeholder — cutover 후 삭제
git mv _imported/vbgw_v2/vbgw-ai services/
git mv _imported/vbgw_v2/vbgw-freeswitch/bridge services/vbgw-bridge
git mv _imported/vbgw_v2/vbgw-freeswitch/orchestrator services/vbgw-orchestrator
git mv _imported/vbgw_v2/vbgw-freeswitch services/freeswitch
git mv _imported/vbgw_v2/charts/vbgw deploy/helm/vbgw
git mv _imported/vbgw_v2/legacy legacy
git mv _imported/vbgw_v2/docs/performance docs/performance
# AgenticOE_v2/docs/*.docx → docs/business/
mkdir -p docs/business
git mv docs/*.docx docs/business/ || true
git mv docs/*.xlsx docs/business/ || true
git mv docs/*.pptx docs/business/ || true
# 옛 docker-compose 들 정리
mkdir -p docker
git mv docker-compose.yml docker/compose.yml
git mv docker-compose.dev.yml docker/compose.dev.yml
git mv docker-compose.integration.yml docker/compose.integration.yml
# vbgw 의 docker-compose 도 통합 — manual merge

# 4) _imported/ 정리
rm -rf _imported

# 5) 옛 deprecate 자산 제거
git rm -rf deploy/helm/agentoe-vbgw    # DEPRECATED 였음

# 6) commit
git commit -m "monorepo: vbgw_v2 import + skeleton 폐지 + 디렉토리 재구조화"
```

### Phase M-3: 경로 / import / 참조 일괄 수정
영향 받는 부분은 §6 표. 자동화 스크립트 (M-3 단독 PR):

```bash
# 1) Python import — backend 코드 안의 'app.grpc_stubs' 는 그대로 (services/backend/app/...)
#    하지만 contracts/gen/python/ 에서 vendoring 하던 부분은 services/backend 가 직접 import 가능
#    (sys.path 추가 또는 pyproject 의 dependency)

# 2) Go module path
#    옛 vbgw-ai 의 go.mod: module vbgw-ai → module github.com/kchul199/agentoe/services/vbgw-ai
#    bridge / orchestrator 동일
#    proto import path 도 변경 필요

# 3) Dockerfile context 경로
#    services/{backend,vbgw-ai,vbgw-bridge,...}/Dockerfile 의 COPY 경로 수정

# 4) Helm values 의 chart path 참조
#    deploy/helm/values/{staging,prod}/*.values.yaml 의 image.repository 정리

# 5) GHA 워크플로 path-filter 갱신
#    paths: ['services/backend/**'] 등

# 6) docs 안의 path 일괄 sed
#    'skeleton/' 제거, 'vbgw_v2/' 를 'services/' 로

# 7) HANDOFF.md / runbook / cross-project-integration.md 갱신 (cross-project 개념 자체 폐지)
```

### Phase M-4: CI/CD 통합
- `validate.yml` 의 path filter 에 services/* 추가
- `build-images.yml` matrix: backend/vbgw-ai/vbgw-bridge/vbgw-orchestrator/frontend (각 Dockerfile 위치)
- `deploy-staging.yml` / `deploy-production.yml` 가 vbgw helm chart 도 같이 deploy
- vbgw_v2 측에 있던 CI/CD 흡수 (파악 필요 — `vbgw_v2/.github/workflows/`)
- contracts: sync 스크립트 폐지 → CI 가 `make gen` 결과가 stale 인지만 확인 (`git diff --exit-code gen/`)

### Phase M-5: 문서 / 메모리 / 마감
- `HANDOFF.md` 전면 갱신 — cross-project 개념 폐지, §11 cross-project 섹션 제거
- `docs/guide/cross-project-integration.md` 폐지 (또는 history 만 남김)
- `.auto-memory/vbgw-cross-project.md` 갱신 — "통합 완료, 더 이상 별도 프로젝트 아님"
- `vbgw_v2` repo 는 read-only archive 처리. README 에 "이 repo 는 agentoe 로 통합. 이력 보존용 archive" 명시.
- 새 repo 의 CI green + 통합 dev smoke OK + 첫 release tag.

## 4. 영향 받는 git 자산

| 항목                          | AgenticOE_v2 (현재) | vbgw_v2 (현재) | 통합 후 |
|-------------------------------|---------------------|-----------------|---------|
| repo 수                        | 1                   | 1               | 1 (`agentoe`) |
| commits (현재)                 | 2                   | 34              | 2+34+merge commits |
| 추적 파일 수 (대략)            | ~378                | ~3,419          | ~3,800 (중복 정리 후) |
| Helm chart                     | 3 (backend/frontend/vbgw-deprecated) | 1 (vbgw) | 3 (backend/frontend/vbgw) |
| docker-compose 파일            | 3 (yml/dev/integration) | 4 (yml/prod/canary/integration) | 3 통합 |
| GHA 워크플로                   | 5                   | (확인 필요)     | 5 (path-filter 매트릭스 확장) |
| go module 수                   | 0                   | 3 (vbgw-ai/bridge/orchestrator) | 3 (path 변경 필요) |
| Python module                  | 1 (backend)         | 0               | 1 (backend) |
| proto canonical 위치           | `skeleton/contracts/` | `vbgw-ai/proto/` (중복 stub) | `contracts/` 단일 |

## 5. 영향 받는 운영 자산 (배포된 환경)

| 자산                          | 변경 영향 | 대응 |
|-------------------------------|-----------|------|
| AWS Terraform state           | 변경 없음 | 계속 사용 |
| EKS cluster                   | 변경 없음 | 계속 사용 |
| ECR repository 이름            | 변경 없음 (`agentoe-{env}/{service}`) | 계속 사용 |
| 기존 Helm release name        | 변경 없음 | 계속 사용 |
| GitHub OIDC IAM role          | repo 이름 바뀌면 trust policy 의 `sub` 갱신 필요 | Terraform PR (1줄) |
| GHA Variables/Secrets         | 새 repo 면 재등록 / 기존 repo 면 그대로 | repo 결정에 따름 |
| Slack webhook                 | 변경 없음 | 계속 사용 |
| PagerDuty integration         | 변경 없음 | 계속 사용 |

## 6. 영향 받는 코드 / 문서 항목 (수정 필요)

| 영역                                              | 작업                                  |
|---------------------------------------------------|---------------------------------------|
| **Python import path**                            | `services/backend/app/...` 그대로 유지. `app/grpc_stubs/voicebot/` 은 그대로 vendor. contracts/ → backend vendoring 자동화 가능. |
| **Go module path** (vbgw-ai/bridge/orchestrator)  | `module github.com/<org>/agentoe/services/<svc>` 로 변경. `replace` 지시문 필요할 수 있음. |
| **Proto import path**                              | go_package 경로 갱신, sync 스크립트 폐지 |
| **Helm chart `agentoe-vbgw/`**                     | 삭제 (DEPRECATED 였음) |
| **Helm chart `deploy/helm/vbgw/`**                 | vbgw_v2/charts/vbgw 가 canonical 위치로 이동 |
| **`docker-compose*.yml`**                          | docker/ 디렉토리로 이동 + vbgw 서비스 통합 |
| **GHA workflows**                                  | path-filter / matrix 확장 |
| **HANDOFF.md §2 (디렉토리 지도)**                  | 전면 재작성 |
| **HANDOFF.md §11 (cross-project)**                 | 삭제 |
| **CLAUDE.md (양쪽)**                               | vbgw_v2/CLAUDE.md 폐지 (root 통합). agentoe 의 CLAUDE.md 가 모든 컨벤션 |
| **`docs/guide/cross-project-integration.md`**      | history 만 남기고 deprecate (또는 삭제) |
| **`scripts/integration/dev-integration.sh`**        | AGENTOE_DIR/VBGW_DIR 분리 폐지, 단일 repo |
| **`scripts/integration/smoke_grpc_client.py`**     | path 변경만, 로직 그대로 |
| **`docs/runbook/dev-integration-test.md`**         | 단일 compose 명령으로 단순화 |
| **`docs/runbook/vbgw-ai-cutover.md`**              | 양 repo PR 절차 → 단일 PR 로 단순화 |
| **`.auto-memory/*`**                               | vbgw-cross-project 메모리 통합 또는 삭제 |
| **vbgw_v2/.github/workflows/**                     | 통합 워크플로로 흡수 |
| **vbgw_v2 의 모든 internal docs**                   | docs/ 로 이동 + 중복 정리 |

## 7. 리스크 + 완화

| 리스크                                                    | 완화                                              |
|----------------------------------------------------------|---------------------------------------------------|
| **git history 손실** (`git mv` 가 detection 못 할 때)      | `git log --follow` 로 검증. 큰 이동은 별도 commit. `git filter-repo` 대안 검토 |
| **Go module path 변경으로 import 깨짐**                    | Phase M-3 안에서 `gofmt`+`go build` 모든 모듈 통과까지 머지 안 함 |
| **CI 시간 폭증** (모든 PR 이 모든 잡 돌림)                 | path-filter 도입 의무. validate-gate 가 skip 잡 success 처리 |
| **vbgw_v2 의 git LFS 사용 (silero_vad.onnx 등 바이너리)**   | merge 전 LFS 설정 확인. AgenticOE_v2 도 LFS 활성화 |
| **branch 정책 충돌** (양쪽이 다른 protection 룰)            | 통합 후 단일 룰. branch protection 재설정 필요 |
| **OIDC IAM role 의 trust policy 가 기존 repo sub 패턴 사용** | 새 repo 면 Terraform PR 로 sub 갱신. 기존 repo 면 무관 |
| **외부 contractor 가 vbgw 만 보던 케이스**                 | charls 가 두 프로젝트 owner 라 무관 (사전 확인 완료) |
| **CI 가 잠시 깨짐 (workflow 통합 도중)**                   | `feat/monorepo-merge` 브랜치에서 충분히 검증 후 main 머지 |

## 8. 일정 (추정)

총 1주일 (실 작업 시간 약 2-3일):

| Phase | 무엇                                        | 예상 |
|-------|---------------------------------------------|------|
| M-1   | 사전 정리 + 백업                            | 0.5일 |
| M-2   | subtree merge + 디렉토리 재구조화            | 0.5일 |
| M-3   | 경로/import/참조 일괄 수정                  | 1일   |
| M-4   | CI/CD 통합                                  | 0.5일 |
| M-5   | 문서/메모리/마감                            | 0.5일 |
| 검증  | dev smoke + staging dry-run                 | 1일   |

→ 운영 cutover 는 통합 검증 후 (= 추가 1주일).

## 9. 결정 게이트 (사용자 승인 필요)

다음 5 가지 중 합의 후 Phase M 실행:

1. **repo 이름** — `agentoe` / `agentoe-platform` / `AgenticOE_v2` 유지?
2. **`skeleton/` 폐지** — yes / no?
3. **vbgw_v2 history 보존 방식** — `git subtree --squash=false` (전체 보존) / `--squash=true` (단일 commit) ?
4. **새 repo vs 기존 repo** — 새 GitHub repo / AgenticOE_v2 에 import?
5. **vbgw-ai cutover 와 monorepo 통합 순서** — 통합 먼저 / cutover 먼저?

내 권장:
1. `agentoe`
2. `skeleton/` 폐지
3. subtree, history 보존
4. AgenticOE_v2 에 import (기존 repo 이어쓰기)
5. **통합 먼저** → cutover 가 단일 PR 로 안전

## 10. 롤백 (긴급)

만약 통합 PR 머지 후 문제 발견 시:

1. **단순 revert** — 통합 PR revert + main force-push (위험. CI 가 빠를 때만).
2. **subtree split** — `git subtree split --prefix=services/vbgw-ai` 로 vbgw 부분만 별도 branch 추출 → vbgw_v2 repo 로 push back.
3. **백업 mirror 복원** — Phase M-1 의 `git clone --mirror` 로 받은 bare copy 를 새 repo 로 push.

권장: 통합 후 **2주간 vbgw_v2 repo 도 동결 (read-only)** 로 두고 모니터링. 안전 확정 후 archive.

## 11. 다음 단계 — 사용자 결정 필요

이 plan 을 검토 후 §9 의 5 가지 결정 + GO/NO-GO 알려주시면 Phase M-1 부터 task 분해 + 실행:

```text
사용자 결정 예시:
  1) repo 이름: agentoe
  2) skeleton/ 폐지: yes
  3) history 보존: subtree (squash=false)
  4) repo: AgenticOE_v2 에 import
  5) 순서: 통합 → cutover
  GO
```

승인 받으면 다음 Phase 들이 자동 생성됩니다:
- Phase M-1: 사전 정리 (3 task)
- Phase M-2: subtree merge + 재구조화 (5 task)
- Phase M-3: 경로/import 일괄 수정 (8 task)
- Phase M-4: CI/CD 통합 (4 task)
- Phase M-5: 문서/메모리/마감 (3 task)
- 통합 검증: dev smoke + staging dry-run (2 task)
