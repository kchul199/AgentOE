# Guide — CI/CD 운영 가이드

> 적용 범위: GitHub Actions 5 개 워크플로 + 5 개 composite action + GitHub OIDC → AWS keyless 인증.

## 1. 워크플로 한눈에

| 파일                     | 트리거                                           | 주요 책임                                              |
|--------------------------|---------------------------------------------------|--------------------------------------------------------|
| `ci.yml`                 | PR + push (main/develop)                          | 백엔드 lint / unit / integration / coverage / docker import-OK |
| `validate.yml`           | PR + push (main)                                  | helm lint+template+kubeconform / tf fmt+validate+tflint / Trivy fs / Hadolint / frontend lint |
| `build-images.yml`       | push main, push v*, manual                        | OIDC → ECR push role → backend/vbgw/frontend matrix build → Trivy 이미지 게이트 → ECR push (IMMUTABLE) |
| `deploy-staging.yml`     | `workflow_run` of build-images on main, manual    | OIDC → eks deploy role → render-values → helm-diff → upgrade --atomic → smoke → Slack |
| `deploy-production.yml`  | push v*, manual                                   | plan(diff) → manual approval → backend canary 10% → bake → promote 전체 → release notes → canary cleanup |

## 2. 흐름 다이어그램

```text
        ┌──── PR ────┐
        │            │
        ▼            ▼
   validate.yml   ci.yml      ← 머지 게이트 (branch protection 필수)
        │            │
        └─────┬──────┘
              ▼
          merge to main
              │
              ▼
        build-images.yml   ── ECR push (sha tag)
              │  workflow_run
              ▼
       deploy-staging.yml  ── helm upgrade --atomic + smoke + Slack
                                                     │
                                                     ▼
                                   ✅ → 사람이 git tag vX.Y.Z 푸시
                                                     │
                                                     ▼
                                            build-images.yml (vX.Y.Z 태그)
                                                     │
                                                     ▼
                                          deploy-production.yml
                                            ├── plan (diff)
                                            ├── 🛑 Manual approval (production env)
                                            ├── canary backend 10%  (5분 bake)
                                            ├── promote all (atomic)
                                            ├── canary cleanup
                                            └── GitHub Release
```

## 3. 트리거 매트릭스

| 이벤트                       | ci | validate | build-images | deploy-staging | deploy-production |
|------------------------------|----|----------|--------------|----------------|-------------------|
| PR open / sync               | ✅ | ✅       | ✗            | ✗              | ✗                 |
| push to develop              | ✅ | ✗        | ✗            | ✗              | ✗                 |
| push to main                 | ✅ | ✅       | ✅           | (auto)         | ✗                 |
| push tag `v*`                | ✗  | ✗        | ✅           | ✗              | ✅                |
| `workflow_dispatch`          | ✗  | ✗        | ✅           | ✅             | ✅                |

## 4. 시크릿 / Variables

GitHub repo → Settings → Secrets and variables → Actions

### Variables (평문 OK — 로그에 노출되어도 됨)

| 키                                  | 값 예시                                                      | 사용처                  |
|-------------------------------------|--------------------------------------------------------------|-------------------------|
| `AWS_REGION`                        | `ap-northeast-2`                                             | 모든 deploy/build       |
| `ECR_REGISTRY`                      | `123456789012.dkr.ecr.ap-northeast-2.amazonaws.com`         | build-images            |
| `ECR_PREFIX_STAGING`                | `agentoe-staging`                                            | build-images (main)     |
| `ECR_PREFIX_PROD`                   | `agentoe-prod`                                               | build-images (tag)      |
| `AWS_ECR_PUSH_ROLE_ARN`             | terraform output `ecr_push_role_arn`                         | build-images            |
| `AWS_EKS_DEPLOY_ROLE_ARN`           | terraform output `eks_deploy_role_arn` (staging)             | deploy-staging          |
| `AWS_EKS_DEPLOY_ROLE_ARN_PROD`      | terraform output `eks_deploy_role_arn` (prod)                | deploy-production       |
| `EKS_CLUSTER_NAME_STAGING`          | terraform output `cluster_name`                              | deploy-staging          |
| `EKS_CLUSTER_NAME_PROD`             | terraform output `cluster_name` (prod)                       | deploy-production       |
| `TF_OUTPUT_S3_URI_STAGING`          | `s3://agentoe-tfstate-staging/outputs/tf.json`               | deploy-staging          |
| `TF_OUTPUT_S3_URI_PROD`             | `s3://agentoe-tfstate-prod/outputs/tf.json`                  | deploy-production       |

### Secrets (비밀)

| 키                          | 용도                                                  |
|-----------------------------|-------------------------------------------------------|
| `SLACK_WEBHOOK_DEPLOY`      | 배포 성공/실패 알림                                   |
| `TF_OUTPUT_STAGING_JSON`    | (옵션) S3 대신 base64 인코딩된 terraform output 직접 주입 |

> **시크릿 ≠ 비밀번호.** 우리는 `aws-actions/configure-aws-credentials` + OIDC 로 AWS 접근 → AWS access key 시크릿 자체를 둘 필요가 없다. 키 누출 위험 0.

## 5. Branch protection (권장)

`main` 브랜치 보호 규칙 — 머지 차단:

- Require a pull request before merging
  - Require approvals: **1+**
  - Dismiss stale approvals on new commits: ✅
- Require status checks to pass:
  - `Validate gate`
  - `Lint & Type Check`        (ci.yml)
  - `Unit Tests`               (ci.yml)
  - `Integration Tests`        (ci.yml)
  - `Frontend lint`            (validate.yml)
- Require linear history: ✅ (rebase merge 강제)
- Restrict who can push: 관리자만 직접 푸시 가능
- Do not allow bypassing the above settings: ✅

태그 보호 (`v*`):

- Settings → Tags → New rule → 패턴 `v*`
- 푸시 가능자: release manager 그룹만

## 6. GitHub Environments

| Environment        | Required reviewers | URL                                          | 용도                           |
|--------------------|--------------------|----------------------------------------------|--------------------------------|
| `staging`          | (없음, 자동)        | https://api-staging.agentoe.io               | deploy-staging                 |
| `production-plan`  | (없음)              | —                                            | helm-diff 결과 미리보기        |
| `production`       | **1+ approver**     | https://api.agentoe.io                       | manual approval + canary/promote |

## 7. 일상적 운영 흐름

### 7.1 기능 추가 → staging 자동 배포

```bash
git checkout -b feat/quota-burst
# 작업
git push -u origin feat/quota-burst
# → PR 열기 → CI + validate 모두 green 확인 → squash merge to main
# → main 푸시 즉시 build-images → deploy-staging 자동
```

### 7.2 staging 검증 후 production 릴리즈

```bash
# main 의 검증된 커밋에 태그
git checkout main && git pull
git tag -s v1.4.0 -m "v1.4.0 — agentic canary 25%"
git push origin v1.4.0
# → build-images (vX.Y.Z 태그)
# → deploy-production "plan" job 결과 확인
# → GitHub Actions UI 에서 production environment "Review deployments" → Approve
# → canary 10% 5분 bake → promote → release notes
```

### 7.3 핫픽스 (긴급 패치)

```bash
git checkout -b hotfix/jwt-clock-skew main
# 패치 + 테스트
git push -u origin hotfix/jwt-clock-skew
# → PR → 1명 approval → squash merge
# → 자동 staging 배포 → 5분 검증
git checkout main && git pull
git tag -s v1.4.1 && git push origin v1.4.1
# → production 승인 후 배포 (canary 옵션 skip 가능)
```

수동 트리거로 canary 생략:

- Actions → Deploy → Production → Run workflow → `image_tag=v1.4.1`, `skip_canary=true`

### 7.4 롤백

자동 롤백:
- helm-deploy 액션이 `--atomic` 사용 → 실패 시 즉시 자동 rollback
- canary bake 단계에서 ERROR 라인 임계 초과 → canary 자동 uninstall

수동 롤백:

```bash
# 클러스터 직접
helm -n agentoe history agentoe-backend
helm -n agentoe rollback agentoe-backend <REV>

# 또는 직전 태그 다시 deploy
gh workflow run deploy-production.yml \
  -f image_tag=v1.3.9 -f skip_canary=true
```

## 8. 이미지 / 태그 정책

- **ECR repository immutability=IMMUTABLE** (Terraform 모듈에서 강제) → 같은 태그 재푸시 차단.
- **태그 형식**:
  - main 푸시 → `<short-sha-12>` (예: `a1b2c3d4e5f6`)
  - tag 푸시 → `vX.Y.Z` (semver) — 동시에 sha 태그도 푸시
- **digest 검증**: ecr-build-push action 이 push 후 `aws ecr describe-images` 로 digest 확인.
- **Trivy 게이트**: CRITICAL+HIGH 발견 시 빌드 실패. 일시 면제 시 PR 에서 `.trivyignore` 추가.

## 9. 흔한 문제 + 대응

| 증상                                                | 원인                                                          | 해결                                                           |
|-----------------------------------------------------|---------------------------------------------------------------|----------------------------------------------------------------|
| `Error: Could not assume role with OIDC`            | Variables 의 ROLE_ARN 오타 / trust policy 의 sub 패턴 불일치 | 워크플로 ref/environment 와 trust policy condition 비교        |
| ECR push `RepositoryImmutableException`             | 같은 태그 재푸시 시도                                         | 새 sha 로 다시 빌드 (커밋 amend / 새 commit)                   |
| Trivy 가 외부 GitHub.com rate limit 으로 실패        | trivy DB 다운로드 한도 초과                                    | `aquasecurity/trivy-action` 의 `cache: true` + `db-repository` 미러 사용 |
| `Helm upgrade timeout`                              | atomic 롤백 후 → readiness 가 5분 안에 충족 안 됨            | startupProbe failureThreshold 늘리거나 image push 가 누락 확인 |
| `imagePullBackOff` (ECR)                            | 노드 IAM 의 ECR pull 권한 누락 (eks managed NG 디폴트엔 있음) | aws-auth ConfigMap / nodegroup IAM role 확인                  |
| Deploy 가 main push 직후 안 시작됨                   | `workflow_run` 이벤트는 default branch 에서 정의된 워크플로만 트리거 | main 에 deploy-staging.yml 머지 확인                           |
| canary uninstall 실패                                | Pod terminating 중                                             | 다음 run 의 cleanup-canary job 이 재처리 — 보통 자동 정리됨    |

## 10. 관찰 / 감사

- **GHA run history**: 모든 OIDC 어슘은 CloudTrail `AssumeRoleWithWebIdentity` 이벤트 — STS 세션 이름에 `github-actions-${run_id}` 박혀 있어 추적 가능.
- **ECR push history**: `aws ecr describe-images --repository-name ...`
- **Helm history**: `helm -n <ns> history <release>`  → revision / chart version / image tag 매핑 보존.
- **Slack 채널**: `#ops-deploy` (배포 알림), `#ops-incident` (페이지)

## 11. 비용 / 성능 메모

- GHA `ubuntu-latest` 분당 과금 — 평균 빌드 시간:
  - validate: 2–3 분 (모든 잡 합산 wall-time)
  - build-images: 8–10 분 (3 서비스 병렬, GHA 캐시 hit 시 4–5 분)
  - deploy-staging: 4 분 (3 차트 순차)
  - deploy-production: 12–15 분 (canary bake 5 분 포함)
- **빌드 캐시**: `cache: type=gha,scope=<service>` — 서비스별 격리. 대부분의 Python 의존성 변경 없으면 30s 안에 끝.

## 12. 다음 개선 후보

- [ ] Renovate / Dependabot 자동 PR + auto-merge (lockfile 만)
- [ ] `aquasecurity/trivy-action` → `cosign` 으로 이미지 서명
- [ ] OPA Gatekeeper / Kyverno — 클러스터에서도 immutable image / non-root 강제
- [ ] PR 에 helm-diff 결과를 주석으로 자동 게시 (현재는 job summary)
- [ ] GitHub Environments 에 `Wait timer` 5분 — production 자동 카나리 promote 게이트
