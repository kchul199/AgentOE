# agentoe-portal Prod 배포 Runbook

> Phase N — N5.3  
> 대상 환경: EKS production  
> 예상 소요시간: ~25분 (canary 관찰 포함)

---

## 전제조건

| 항목 | 확인 |
|------|------|
| `portal-build.yml` CI 녹색 — ECR prod 이미지 push 완료 (tag push) | □ |
| Trivy 이미지 스캔 통과 (CRITICAL=0) — `portal-scan` job 결과 | □ |
| `portal-staging.agentoe.io/healthz` HTTP 200 확인 | □ |
| GitHub Environment `production` 승인 완료 (또는 `--auto-approve` 사용) | □ |
| EKS prod kubeconfig 설정 (`kubectl cluster-info --context prod` 동작) | □ |
| `deploy/helm/values/prod/portal.values.yaml` 의 `ACM_CERT_ARN_PROD` 교체 완료 | □ |
| PagerDuty maintenance window 열기 (배포 전 — 알람 억제) | □ |

---

## 빠른 실행 (자동 스크립트)

```bash
# 1. 환경변수 준비
export ECR_REGISTRY="123456789012.dkr.ecr.ap-northeast-2.amazonaws.com"
export AWS_REGION="ap-northeast-2"
export KUBECONFIG="~/.kube/config-prod"
export PORTAL_HOSTNAME="portal.agentoe.io"
export STAGING_HOSTNAME="portal-staging.agentoe.io"
export SLACK_WEBHOOK_URL="https://hooks.slack.com/..."   # 선택
export PD_API_KEY="..."                                  # 선택

# 2. dry-run 으로 먼저 검증
./scripts/portal_prod_deploy.sh --image-tag <TAG> --dry-run

# 3. 실제 배포
./scripts/portal_prod_deploy.sh --image-tag <TAG>
```

### CI 자동 배포 (GitHub Actions)

`v*` 태그 push → `portal-build.yml` → `portal-prod-deploy` job (GitHub Environment approval)

```bash
# 예시: v1.3.0 태그 push
git tag v1.3.0 && git push origin v1.3.0
```

---

## 5단계 게이트 상세

### Gate 1 — Staging 헬스 검사

`https://portal-staging.agentoe.io/healthz` HTTP 200 응답 필수.

```bash
curl -fsSk https://portal-staging.agentoe.io/healthz
# 응답: {"status":"ok"}
```

staging 이 내려가 있으면 prod 배포 중단. 긴급 패치 시 `--skip-staging-check` (팀장 승인 필수).

### Gate 2 — ECR CVE CRITICAL=0

```bash
aws ecr describe-image-scan-findings \
  --repository-name "agentoe-prod/ops-portal" \
  --image-id imageTag=<TAG> \
  --region ap-northeast-2 \
  --query 'imageScanFindings.findingSeverityCounts'
```

CRITICAL > 0 이면 배포 중단. 베이스 이미지 업데이트 또는 패키지 업그레이드 필요.

### Gate 3 — 수동 승인

스크립트 실행 시 `DEPLOY` 입력 필요. CI 에서는 GitHub Environment `production` 리뷰어 승인으로 대체.

### Gate 4 — Canary 10% → 50% → 100%

각 단계마다 ~2분 관찰 후 Prometheus error_rate 게이트 (`< 1.0%`).

| 단계 | replicas | 관찰 시간 | error_rate 기준 |
|------|----------|-----------|-----------------|
| 10%  | 1/3      | 2분       | < 1.0%          |
| 50%  | 2/3      | 2분       | < 1.0%          |
| 100% | 3/3      | 1분       | < 1.0%          |

어느 단계에서든 초과 시 자동 `helm rollback`.

### Gate 5 — Prod smoke test

`https://portal.agentoe.io/healthz` HTTP 200, 최대 60초 대기.

---

## 수동 단계별 배포

### 1. 이미지 태그 확인

```bash
aws ecr list-images \
  --repository-name "agentoe-prod/ops-portal" \
  --region ap-northeast-2 \
  --query 'imageIds[*].imageTag' \
  --output table

IMAGE_TAG="<위에서 확인한 태그>"
ECR_REGISTRY="123456789012.dkr.ecr.ap-northeast-2.amazonaws.com"
```

### 2. ECR 인증

```bash
aws ecr get-login-password --region ap-northeast-2 \
  | kubectl create secret docker-registry ecr-prod \
      --docker-server="${ECR_REGISTRY}" \
      --docker-username=AWS \
      --docker-password-stdin \
      --namespace=portal \
      --dry-run=client -o yaml \
  | kubectl apply -f -
```

### 3. namespace 생성 (최초 1회)

```bash
kubectl create namespace portal --dry-run=client -o yaml | kubectl apply -f -
```

### 4. Canary 10%

```bash
helm upgrade --install agentoe-portal deploy/helm/agentoe-portal \
  --namespace portal \
  -f deploy/helm/values/prod/portal.values.yaml \
  --set "image.repository=${ECR_REGISTRY}/agentoe-prod/ops-portal" \
  --set "image.tag=${IMAGE_TAG}" \
  --set "image.pullPolicy=Always" \
  --set "replicaCount=1" \
  --cleanup-on-fail
kubectl rollout status deployment/agentoe-portal -n portal --timeout=3m

# 2분 관찰 → Prometheus error_rate 확인
sleep 120
curl -G 'http://prometheus:9090/api/v1/query' \
  --data-urlencode 'query=100*sum(rate(http_requests_total{service="agentoe-portal",status=~"5.."}[2m]))/sum(rate(http_requests_total{service="agentoe-portal"}[2m]))' \
  | jq '.data.result[0].value[1]'
```

### 5. Canary 50%

```bash
helm upgrade agentoe-portal deploy/helm/agentoe-portal \
  --namespace portal \
  -f deploy/helm/values/prod/portal.values.yaml \
  --set "image.repository=${ECR_REGISTRY}/agentoe-prod/ops-portal" \
  --set "image.tag=${IMAGE_TAG}" \
  --set "replicaCount=2"
kubectl rollout status deployment/agentoe-portal -n portal --timeout=3m
# 2분 관찰 후 Gate 확인 (위와 동일)
```

### 6. 100% (atomic)

```bash
helm upgrade --install agentoe-portal deploy/helm/agentoe-portal \
  --namespace portal \
  -f deploy/helm/values/prod/portal.values.yaml \
  --set "image.repository=${ECR_REGISTRY}/agentoe-prod/ops-portal" \
  --set "image.tag=${IMAGE_TAG}" \
  --atomic \
  --timeout 5m \
  --cleanup-on-fail \
  --history-max 10
kubectl rollout status deployment/agentoe-portal -n portal --timeout=5m
```

### 7. Smoke test

```bash
curl -fsSk https://portal.agentoe.io/healthz
```

---

## 롤백

```bash
# 즉시 롤백 (직전 릴리스)
helm rollback agentoe-portal -n portal

# 특정 revision
helm history agentoe-portal -n portal
helm rollback agentoe-portal <REVISION> -n portal

# 상태 확인
kubectl rollout status deployment/agentoe-portal -n portal
kubectl get pods -n portal -l app.kubernetes.io/name=agentoe-portal
```

---

## 트러블슈팅

### Canary error_rate 초과

1. `kubectl logs -n portal -l app.kubernetes.io/name=agentoe-portal --tail=100` 확인
2. 새 버전에서 500 응답 원인 파악
3. `helm rollback agentoe-portal -n portal` 즉시 실행
4. backend 의존성 (MongoDB / Redis / Prometheus) 연결 상태 확인

### Pod ImagePullBackOff

```bash
kubectl describe pod -n portal -l app.kubernetes.io/name=agentoe-portal
```
- `ECR_REGISTRY` 값 / 이미지 태그 오기입 확인
- `ecr-prod` imagePullSecret 만료 → §2 재실행

### Helm atomic 실패 / rollback 후 상태

```bash
helm status agentoe-portal -n portal
helm history agentoe-portal -n portal
kubectl events -n portal --sort-by='.lastTimestamp' | tail -30
```

---

## 배포 후 체크리스트

```
□ helm status → deployed
□ kubectl get pods -n portal 전체 Running (3개)
□ https://portal.agentoe.io/healthz HTTP 200
□ 로그인 페이지 정상 렌더링
□ Dashboard SSE LIVE 배지 표시
□ Alerts 페이지 Alertmanager 연결 확인
□ Config 페이지 3개 환경 설정 조회 성공
□ audit.tail SSE 수신 확인
□ PagerDuty maintenance window 종료 (--pd-service-id)
□ Prometheus prod dashboard 이상 없음
□ Slack #ops 배포 완료 알림 확인
```

---

## 관련 파일

| 파일 | 역할 |
|------|------|
| `deploy/helm/agentoe-portal/` | Helm chart |
| `deploy/helm/values/prod/portal.values.yaml` | prod 환경값 (N5.3: 3 replicas, 500m CPU) |
| `scripts/portal_prod_deploy.sh` | 이 runbook 의 자동화 스크립트 |
| `scripts/portal_staging_deploy.sh` | staging 배포 (prod 전 반드시 통과) |
| `.github/workflows/portal-build.yml` | CI — `portal-prod-deploy` job 포함 |
| `docs/runbook/portal-staging-deploy.md` | Staging 배포 runbook |
