# Runbook — monorepo 머지 + vbgw cutover Stage A

> 단계별 사용자 host 작업. 각 단계가 끝나야 다음 단계 진행.
> 의존: Phase M (monorepo 통합) 완료 = `feat/monorepo-merge` branch 가 sandbox 에서 만들어짐.

## Stage 0 — Push (sandbox 가 못 한 부분)

```bash
cd ~/AgenticOE_v2

# 1) 작업 branch + 백업 + tag 한 번에
git push -u origin feat/monorepo-merge
git push origin backup/pre-monorepo
git push origin v0-pre-monorepo

# 2) 검증
git log --oneline origin/feat/monorepo-merge | head -10
git log --oneline origin/main | head -3
```

## Stage 1 — PR open

GitHub 에 가서 PR 만들기:
**URL**: `https://github.com/kchul199/AgentOE/compare/main...feat/monorepo-merge`

### PR 제목
```
feat(monorepo): merge vbgw_v2 into AgenticOE_v2 (Phase M)
```

### PR description (복사)

```markdown
## 요약

vbgw_v2 와 AgenticOE_v2 를 단일 monorepo `agentoe` 로 통합. cross-project sync 폐지, 단일 CI/CD,
단일 docker-compose, 단일 진실 소스 docs.

## 변경 통계

- **41 commits** (vbgw_v2 의 34 history 보존 + monorepo baseline + 4 refactor commits)
- **+3,800 files** (vbgw 흡수 + 재구조화)
- **-2 repos** (vbgw_v2 archive 예정)

## 5 commits 시퀀스

| Commit | Phase | 내용 |
|--------|-------|------|
| `83ccbf5` | baseline | Phase 1-D 누적 작업물 (pre-monorepo baseline) |
| `7372729` | M-2.1 | git subtree add (vbgw_v2 origin/main, 34 commits 보존) |
| `028cd36` | M-2.2~5 | skeleton/ 폐지 + services/ 재구조화 + deprecated 정리 |
| `2167661` | M-3 | Go module path / proto 단일화 / docker-compose / docs sed |
| `b2543b4` | M-4+M-5 | CI/CD 통합 + HANDOFF/CLAUDE 재작성 |

## 새 디렉토리 구조

```
agentoe/
├── services/{backend,frontend,vbgw-ai,vbgw-bridge,vbgw-orchestrator,freeswitch,_test-stub}
├── contracts/{proto,gen}/         # canonical proto + generated stub
├── deploy/{terraform,k8s-bootstrap,helm,observability}
├── docker/compose.{backend,vbgw,integration}*.yml
├── docs/{HANDOFF,business,adr,guide,reference,runbook,performance,reports}
├── scripts/integration/
├── legacy/                         # 옛 vbgw C++ (참조용)
└── .github/{workflows,actions}
```

## 주요 결정사항

1. **subtree merge (squash=false)** — vbgw_v2 의 34 commit history 모두 보존
2. **skeleton/ 폐지** — 루트로 끌어올림 (history 적인 이름)
3. **Go module path** — `github.com/kchul199/agentoe/services/<svc>`
4. **Proto 단일화** — 3 곳 중복 → `contracts/gen/go/voicebot/` 한 곳, sync 스크립트 폐지
5. **CI/CD 통합** — 5-services matrix (backend/frontend/vbgw-ai/vbgw-bridge/vbgw-orchestrator)
6. **Helm namespace 분리** — backend/frontend → `agentoe[-staging]`, vbgw → `vbgw[-staging]`

## 사전 정적 검증 통과 (sandbox 에서)

| 검증 | 결과 |
|------|------|
| GHA workflow YAML parse | 10/10 OK |
| Helm values + chart YAML | 13/13 OK |
| PrometheusRule (65 rules) | 0 errors |
| Terraform HCL | 36/36 OK |
| Grafana dashboard JSON | 4/4 OK |
| Docker compose | 6/6 OK |

## ⚠️ Merge 시 주의 — 반드시 "Create a merge commit"

> **squash / rebase 금지.** subtree merge 의 history 가 보존되어야 함.
> GitHub UI 의 merge 옵션에서 **"Create a merge commit"** 선택.

## Test plan

- [ ] CI green (`validate.yml`, `ci.yml`)
- [ ] Reviewer 1명 approval
- [ ] Merge commit (squash/rebase 금지)
- [ ] 머지 후 `main` branch 의 commit graph 검증 (`git log --graph --oneline | head -50`)

## After-merge actions

1. `vbgw_v2` repo archive (별도 PR/issue 차단)
2. (선택) repo rename: `AgentOE` → `agentoe`
3. cutover Stage A 시작 (`docs/runbook/monorepo-merge-and-cutover.md` Stage 3)
```

## Stage 2 — 머지 후 마무리

### 2.1 vbgw_v2 archive

```bash
cd ~/vbgw_v2
rm -f .git/index.lock                      # sandbox 가 풀지 못했던 것
# GitHub Web UI 가서:
#   Settings → Danger Zone → Archive this repository
# 이력 보존, 새 PR/issue 차단. 재활성화 가능.
```

### 2.2 (선택) repo rename

```bash
# GitHub Web UI:
#   Settings → Repository name: AgentOE → agentoe
# GitHub 가 redirect 자동 설정. clone URL 정합성:
cd ~/AgenticOE_v2
git remote set-url origin git@github.com:kchul199/agentoe.git
git remote -v   # 검증
```

### 2.3 main branch protection 갱신

Settings → Branches → main:
- Required status checks 갱신 (path-filter 로 일부 잡 skip 가능 — `validate-gate` 필수, 나머지 optional):
  - `Validate gate`
  - `Lint & Type Check`
  - `Unit Tests`
  - `Integration Tests`

### 2.4 GitHub Variables / Secrets 점검

`docs/guide/ci-cd.md` §4 의 12개 Variables + 2개 Secrets 가 모두 등록돼 있는지 확인:

```bash
# CLI 로 변수 목록 (gh cli)
gh variable list -R kchul199/agentoe
gh secret list -R kchul199/agentoe
```

누락 시 추가:
```bash
gh variable set AWS_REGION --body 'ap-northeast-2'
gh variable set ECR_REGISTRY --body '<account>.dkr.ecr.ap-northeast-2.amazonaws.com'
# ... (12개)
gh secret set SLACK_WEBHOOK_DEPLOY < /tmp/slack-webhook.txt
```

## Stage 3 — cutover Stage A 시작 (staging 100%)

다음 runbook 으로:
- `docs/runbook/dev-integration-test.md` — dev smoke 3회 OK 확인
- `docs/runbook/vbgw-ai-cutover.md` Stage A — staging 에 100% backend 전환

자동화 스크립트:
```bash
./scripts/cutover/stage-a-staging.sh
```

이 스크립트가 다음을 일관 실행:
1. preflight (kubectl context = staging, backend gRPC SERVING 확인)
2. helm upgrade vbgw — `bridge.grpcAiAddr=agentoe-backend.agentoe-staging:50051`
3. 합성 통화 5건 (`scripts/integration/synthetic-call.sh`)
4. 5분 모니터링 (setup ratio / mid-call drop / pipeline error)
5. 게이트 통과 → success / 실패 시 자동 롤백

## Stage 4 — Stage B-D 는 별도 진행

- Stage B: prod 10% canary (24h bake) — 사람 의사결정 후 별도 명령
- Stage C: prod 50% canary (12h bake)
- Stage D: prod 100% promote
- Stage E: vbgw-ai deprecate

자세한 절차: `docs/runbook/vbgw-ai-cutover.md`.

## 롤백 (Stage A 실패 시)

```bash
helm -n vbgw-staging upgrade vbgw deploy/helm/vbgw \
  -f deploy/helm/values/staging/vbgw.values.yaml \
  --set bridge.grpcAiAddr=ai-service:50051 \
  --set bridge.canary.enabled=false \
  --wait --timeout 5m

# 진행 중 통화는 자연 종료까지 보존. 새 통화는 vbgw-ai 로.
```

## 관련 문서

- `docs/HANDOFF.md` §11 — monorepo 통합 직후 host 작업
- `docs/guide/monorepo-migration-plan.md` — Phase M 의 5 phase 계획서
- `docs/runbook/dev-integration-test.md` — dev smoke 절차
- `docs/runbook/vbgw-ai-cutover.md` — 4 stage cutover 자세히
- `docs/runbook/grpc-stream-debug.md` — gRPC 흐름 디버깅
