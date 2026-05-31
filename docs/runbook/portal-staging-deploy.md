# agentoe-portal Staging 배포 Runbook

> Phase N — N4.3  
> 대상 환경: EKS staging  
> 예상 소요시간: ~10분 (이미지 빌드 완료 후 기준)

---

## 전제조건

| 항목 | 확인 |
|------|------|
| `portal-build.yml` CI 녹색 (ECR 이미지 push 완료) | □ |
| AWS OIDC 역할 또는 Access Key 발급 (ECR read 권한) | □ |
| EKS staging kubeconfig 설정 (`kubectl cluster-info` 동작) | □ |
| `deploy/helm/values/staging/portal.values.yaml` 의 `ACM_CERT_ARN` 교체 완료 | □ |
| staging 도메인 DNS (portal-staging.agentoe.io → internal ALB) 설정 | □ |

---

## 빠른 실행 (자동 스크립트)

```bash
# 1. 환경변수 준비
export ECR_REGISTRY="123456789012.dkr.ecr.ap-northeast-2.amazonaws.com"
export AWS_REGION="ap-northeast-2"
export KUBECONFIG="~/.kube/config-staging"
export PORTAL_HOSTNAME="portal-staging.agentoe.io"

# 2. dry-run 으로 먼저 검증
./scripts/portal_staging_deploy.sh --dry-run

# 3. 실제 배포 (특정 태그 지정)
./scripts/portal_staging_deploy.sh --image-tag <GIT_SHA_12>

# 또는 최신 main HEAD 로 자동 결정
./scripts/portal_staging_deploy.sh
```

스크립트가 수행하는 단계:
1. Preflight (kubectl / helm / aws CLI, ECR 이미지 존재, helm lint)
2. ECR 이미지 확인
3. ECR 인증 + Kubernetes namespace `portal` 준비
4. `helm upgrade --install` (atomic, 5분 timeout)
5. Rollout 상태 대기
6. Smoke test (`/healthz` HTTP 200, 최대 60초 대기)
7. Slack 알림 (선택)

---

## 수동 단계별 배포

### 1. 이미지 태그 결정

```bash
# CI 에서 push 된 태그 확인
aws ecr list-images \
  --repository-name "agentoe-staging/ops-portal" \
  --region ap-northeast-2 \
  --query 'imageIds[*].imageTag' \
  --output table

IMAGE_TAG="<위에서 확인한 태그>"
ECR_REGISTRY="123456789012.dkr.ecr.ap-northeast-2.amazonaws.com"
```

### 2. ECR 인증

```bash
aws ecr get-login-password --region ap-northeast-2 \
  | kubectl create secret docker-registry ecr-staging \
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

### 4. Helm lint (배포 전 필수)

```bash
helm lint deploy/helm/agentoe-portal \
  -f deploy/helm/values/staging/portal.values.yaml
```

### 5. helm upgrade --install

```bash
helm upgrade --install agentoe-portal deploy/helm/agentoe-portal \
  --namespace portal \
  -f deploy/helm/values/staging/portal.values.yaml \
  --set "image.repository=${ECR_REGISTRY}/agentoe-staging/ops-portal" \
  --set "image.tag=${IMAGE_TAG}" \
  --set "image.pullPolicy=Always" \
  --atomic \
  --timeout 5m \
  --cleanup-on-fail \
  --history-max 5
```

### 6. Rollout 확인

```bash
kubectl rollout status deployment/agentoe-portal -n portal --timeout=3m
kubectl get pods -n portal -l app.kubernetes.io/name=agentoe-portal
```

### 7. Smoke test

```bash
# 클러스터 내부에서 (ALB DNS 미설정 시)
kubectl port-forward -n portal svc/agentoe-portal 8080:80 &
curl -fs http://localhost:8080/healthz

# 외부 (ALB 설정 후)
curl -fsSk https://portal-staging.agentoe.io/healthz
```

---

## 롤백

```bash
# 직전 릴리스로 롤백
helm rollback agentoe-portal -n portal

# 특정 revision 으로 롤백
helm history agentoe-portal -n portal
helm rollback agentoe-portal <REVISION> -n portal

# 롤백 후 상태 확인
kubectl rollout status deployment/agentoe-portal -n portal
```

---

## 트러블슈팅

### Pod ImagePullBackOff

```bash
kubectl describe pod -n portal -l app.kubernetes.io/name=agentoe-portal
```
- `ECR_REGISTRY` 값 / 이미지 태그 오기입 확인
- `ecr-staging` imagePullSecret 만료 → §2 재실행

### nginx 502 Bad Gateway

```bash
kubectl logs -n portal -l app.kubernetes.io/name=agentoe-portal --tail=50
```
- `BACKEND_UPSTREAM` 값이 backend service DNS 와 일치하는지 확인
- `portal.values.yaml` → `backendUpstream: "http://agentoe-backend.default.svc.cluster.local"`

### SSE 연결 즉시 끊김

- ALB `idle_timeout.timeout_seconds=3600` 적용 확인
  ```bash
  kubectl describe ingress -n portal agentoe-portal | grep idle
  ```
- `nginx.conf` 의 `/api/v1/stream/*` 경로 `proxy_buffering off` 확인 (ConfigMap)

### helm upgrade 실패 (atomic rollback)

`--atomic` 플래그로 실패 시 자동 롤백됨.

```bash
helm status agentoe-portal -n portal    # 현재 상태
helm history agentoe-portal -n portal   # 릴리스 이력
kubectl events -n portal --sort-by='.lastTimestamp' | tail -20
```

---

## 체크리스트 (배포 후)

```
□ helm status 가 deployed 상태
□ kubectl get pods -n portal 전체 Running
□ /healthz HTTP 200
□ 로그인 페이지 (/) 정상 렌더링
□ Dashboard 페이지 — SSE LIVE 배지 표시
□ Alerts 페이지 — Alertmanager 연결 확인
□ 감사 로그 페이지 — audit.tail SSE 수신 확인
□ Slack/PagerDuty 에 이상 알람 없음
```

---

## 관련 파일

| 파일 | 역할 |
|------|------|
| `deploy/helm/agentoe-portal/` | Helm chart |
| `deploy/helm/values/staging/portal.values.yaml` | staging 환경값 |
| `services/ops-portal/Dockerfile` | 빌드 이미지 |
| `services/ops-portal/nginx.conf` | nginx 설정 템플릿 |
| `.github/workflows/portal-build.yml` | CI (이미지 빌드 + ECR push) |
| `scripts/portal_staging_deploy.sh` | 이 runbook 의 자동화 스크립트 |
