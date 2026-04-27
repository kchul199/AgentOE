# Runbook — Staging 환경 0 → 1 브링업

> **목표:** 빈 AWS 계정 + 빈 MongoDB Atlas Org 에서 시작해, 약 60–90 분 안에 staging 클러스터에 backend / vbgw / frontend 가 정상 응답 가능한 상태까지 도달.
> **선행:** GitHub repo 의 `skeleton/deploy/` 가 의도한 모양으로 머지되어 있을 것.

---

## 0. 사전 준비 (대략 10 분)

도구 체크 — 로컬에서 한 번 실행:

```bash
terraform version    # >= 1.7
aws --version        # >= 2.15
kubectl version --client
helm version         # >= 3.14
helm plugin install https://github.com/databus23/helm-diff   # diff 용 (선택)
jq --version
```

자격 증명:

```bash
aws sso login --profile agentoe-staging   # 또는 IAM 키
export AWS_PROFILE=agentoe-staging
export AWS_REGION=ap-northeast-2
```

MongoDB Atlas Programmatic API Key (Project Owner):

```bash
export MONGODB_ATLAS_PUBLIC_KEY=...
export MONGODB_ATLAS_PRIVATE_KEY=...
export TF_VAR_atlas_org_id=...
export TF_VAR_atlas_project_id=...
```

도메인 — Route53 hosted zone `agentoe.io` 가 미리 존재해야 함. 없으면 `aws route53 create-hosted-zone --name agentoe.io ...` 로 먼저 생성.

---

## 1. Terraform state 백엔드 (5 분)

```bash
cd skeleton/deploy/terraform/bootstrap-state
terraform init
terraform apply -auto-approve
```

생성물: S3 버킷 + DynamoDB lock 테이블. 한 번만 만들면 끝.

---

## 2. 인프라 프로비저닝 (35–45 분)

```bash
cd skeleton/deploy/terraform/environments/staging

cp terraform.tfvars.example terraform.tfvars
# 편집: aws_region, vpc_cidr, atlas_*, route53_zone_id, project_name, env=staging

terraform init -backend-config="key=staging/terraform.tfstate"
terraform plan -out tf.plan
terraform apply tf.plan
```

시간 분포 (대략):
- VPC + endpoints: 3 분
- EKS control plane: 12–15 분
- EKS managed node group: 4–6 분
- ECR / Secrets / IAM / KMS: 1 분
- ElastiCache Redis: 8–12 분
- MongoDB Atlas M10: 7–10 분
- ACM cert DNS validation: 2–5 분

**완료 후 output 캡처:**

```bash
terraform output -json > /tmp/agentoe-tf-staging.json
cat /tmp/agentoe-tf-staging.json | jq 'keys'
# 기대 키: ecr_registry, atlas_srv_host, redis_primary_host,
#         backend_irsa_role_arn, vbgw_irsa_role_arn,
#         eso_irsa_role_arn, alb_controller_role_arn,
#         external_dns_role_arn, acm_certificate_arn, cluster_name, ...
```

kubeconfig 갱신:

```bash
aws eks update-kubeconfig \
  --name "$(jq -r '.cluster_name.value' /tmp/agentoe-tf-staging.json)" \
  --region "$AWS_REGION" \
  --alias agentoe-staging

kubectl config use-context agentoe-staging
kubectl get nodes   # system NG 1개 노드 Ready 확인
```

---

## 3. 클러스터 부트스트랩 (12–18 분)

```bash
cd ../../../k8s-bootstrap

# 환경 변수 (Makefile 이 사용)
export ALB_CONTROLLER_ROLE_ARN=$(jq -r '.alb_controller_role_arn.value' /tmp/agentoe-tf-staging.json)
export EXTERNAL_DNS_ROLE_ARN=$(jq  -r '.external_dns_role_arn.value'   /tmp/agentoe-tf-staging.json)
export ESO_ROLE_ARN=$(jq          -r '.eso_irsa_role_arn.value'        /tmp/agentoe-tf-staging.json)
export CLUSTER_NAME=$(jq          -r '.cluster_name.value'             /tmp/agentoe-tf-staging.json)
export VPC_ID=$(jq                -r '.vpc_id.value'                   /tmp/agentoe-tf-staging.json)
export AWS_REGION
export ROUTE53_ZONE_ID=$(jq       -r '.route53_zone_id.value'          /tmp/agentoe-tf-staging.json)
export ACME_EMAIL=ops@agentoe.io

make bootstrap   # = metrics-server + alb-controller + cert-manager
                 #   + external-secrets + ingress-nginx + karpenter + monitoring
make verify      # 모든 deployment ready 인지 점검
```

설치 후 확인:

```bash
kubectl -n kube-system get deploy aws-load-balancer-controller -o wide
kubectl -n cert-manager get pods
kubectl -n external-secrets get pods
kubectl -n monitoring get pods | head
```

ClusterSecretStore + ClusterIssuer 적용:

```bash
envsubst < manifests/cluster-secret-store-aws.yaml | kubectl apply -f -
envsubst < manifests/cluster-issuer-letsencrypt.yaml | kubectl apply -f -

kubectl get clustersecretstore aws-secrets-manager
kubectl get clusterissuer letsencrypt-prod letsencrypt-staging
```

---

## 4. 시크릿 적재 (5 분)

Terraform 이 만든 secret slot 은 빈 random_password 또는 placeholder. 실제 운영 키 채우기:

```bash
PREFIX="agentoe/staging"

# Groq
aws secretsmanager update-secret \
  --secret-id "$PREFIX/groq_api_key" \
  --secret-string "gsk_..."

# Google STT/TTS service account JSON
aws secretsmanager update-secret \
  --secret-id "$PREFIX/google_application_credentials" \
  --secret-string "$(cat google-sa.json)"

# JWT signing key (HS256 데모용; 운영은 KMS / RS256 권장)
aws secretsmanager update-secret \
  --secret-id "$PREFIX/jwt_secret" \
  --secret-string "$(openssl rand -hex 32)"
```

`redis_auth_token`, `mongo_app_password` 는 Terraform 이 자동 생성해 두었으므로 건드릴 필요 X — 단, Atlas DB user 비번은 Atlas 콘솔 생성 시 동일 값 적용되었는지 확인.

---

## 5. 컨테이너 이미지 빌드 + 푸시 (10–20 분, CI 가 있으면 자동)

로컬 빌드 예시 (CI 가 없을 때):

```bash
ECR_REG=$(jq -r '.ecr_registry.value' /tmp/agentoe-tf-staging.json)
SHA=$(git rev-parse --short HEAD)

aws ecr get-login-password --region "$AWS_REGION" | \
  docker login --username AWS --password-stdin "$ECR_REG"

docker build -t "$ECR_REG/agentoe-staging/backend:$SHA"  ./skeleton/backend
docker build -t "$ECR_REG/agentoe-staging/vbgw:$SHA"     ./skeleton/vbgw
docker build -t "$ECR_REG/agentoe-staging/frontend:$SHA" ./skeleton/frontend

docker push "$ECR_REG/agentoe-staging/backend:$SHA"
docker push "$ECR_REG/agentoe-staging/vbgw:$SHA"
docker push "$ECR_REG/agentoe-staging/frontend:$SHA"
```

---

## 6. Helm 차트 배포 (5–8 분)

```bash
cd skeleton/deploy/helm

# REPLACE_* 토큰 채우기
ENV=staging make render-values
git diff values/staging   # 검수: ARN, host 가 채워졌는지

# 이미지 tag 주입 (helm --set 으로 chart 별)
SHA=$(git rev-parse --short HEAD)

# PriorityClass 등 공유 리소스
make shared

# 차트 lint → template 검수 → 적용
ENV=staging make lint
ENV=staging make template > /tmp/render.yaml
less /tmp/render.yaml

ENV=staging helm upgrade --install agentoe-backend ./agentoe-backend \
  -n agentoe-staging --create-namespace \
  -f values/staging/backend.values.yaml \
  --set image.tag=$SHA --wait --timeout 5m

ENV=staging helm upgrade --install agentoe-vbgw ./agentoe-vbgw \
  -n agentoe-staging \
  -f values/staging/vbgw.values.yaml \
  --set image.tag=$SHA --wait --timeout 5m

ENV=staging helm upgrade --install agentoe-frontend ./agentoe-frontend \
  -n agentoe-staging \
  -f values/staging/frontend.values.yaml \
  --set image.tag=$SHA --wait --timeout 3m
```

---

## 7. 상태 점검 (5 분)

### 7.1 Pod / 서비스

```bash
kubectl -n agentoe-staging get pods,svc,ingress,hpa,pdb
kubectl -n agentoe-staging get externalsecret
kubectl -n agentoe-staging get certificate     # cert-manager TLS (사용 시)
```

기대:
- 각 deployment Pod READY
- ExternalSecret `STATUS=SecretSynced`, `READY=True`
- Ingress `ADDRESS` 컬럼에 ALB hostname 채워짐 (1–2 분 소요)

### 7.2 DNS / 인증서

```bash
dig +short api-staging.agentoe.io      # ALB hostname 의 alias
dig +short app-staging.agentoe.io
curl -I https://api-staging.agentoe.io/api/v1/livez   # 200 + TLS valid
```

external-dns 가 ALB hostname 으로 ALIAS 레코드 자동 생성 — 안 되면 IRSA 권한 / hosted zone ID 환경변수 점검.

### 7.3 데이터 plane 연결

```bash
# Pod 내부에서 직접 시도
POD=$(kubectl -n agentoe-staging get pod -l app.kubernetes.io/name=agentoe-backend -o name | head -1)
kubectl -n agentoe-staging exec -it $POD -- python - <<'PY'
import os, asyncio, motor.motor_asyncio, redis.asyncio as r
async def main():
    print("MONGODB_URI =", os.environ["MONGODB_URI"][:60], "…")
    cli = motor.motor_asyncio.AsyncIOMotorClient(os.environ["MONGODB_URI"])
    print("mongo ping →", await cli.admin.command("ping"))
    rc = r.from_url(os.environ["REDIS_URL"], decode_responses=True)
    print("redis ping →", await rc.ping())
asyncio.run(main())
PY
```

### 7.4 Prometheus / 메트릭

```bash
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090 &
open http://localhost:9090/targets
# agentoe-backend, agentoe-vbgw 가 UP 인지 확인
```

### 7.5 합성 트래픽

```bash
TOKEN=$(./skeleton/scripts/issue-test-jwt.sh)   # JWT_SECRET 로 서명한 테스트 토큰

curl -s https://api-staging.agentoe.io/api/v1/healthz \
  -H "Authorization: Bearer $TOKEN" | jq .

# WebSocket smoke
wscat -c "wss://api-staging.agentoe.io/api/v1/ws?token=$TOKEN"
> {"type":"ping"}
```

---

## 8. 자주 마주치는 실패 + 대응

| 증상                                         | 원인 / 처치                                                                             |
|----------------------------------------------|-----------------------------------------------------------------------------------------|
| Pod `CrashLoopBackOff`, log 에 Mongo SSL fail | NAT EIP 가 Atlas allowlist 에 안 들어감 — terraform `nat_public_ips` output 확인 후 재apply |
| ExternalSecret 동기화 실패 (`SecretSyncedError`) | ESO IRSA role 의 secretsmanager `:Get*` 권한 prefix 가 secret 실제 ARN 과 다름           |
| ALB Ingress 가 ADDRESS 안 받음               | aws-load-balancer-controller 로그 확인 — IRSA / 서브넷 태그 (`kubernetes.io/role/elb`) 누락 가능 |
| `READY 0/1` 인 backend, log "JWKS unreachable" | JWT_ISSUER / JWKS_URL 도달 실패 — 임시로 NetworkPolicy egress 0.0.0.0/0:443 허용된지 확인 |
| Helm install hang                            | `kubectl describe` 로 PVC pending / image pull fail 확인. ECR pull permission 은 노드 IAM 에 박혀 있어야 |
| `cert-manager` Certificate `Issuing` 영원히   | Route53 TXT 레코드 권한 (DNS01) — external-dns / cert-manager 의 IRSA 별도 확인          |
| HPA `TARGETS = <unknown>`                    | metrics-server Pod 상태 확인. customMetric 은 Prometheus Adapter 추가 후 켜기            |

---

## 9. 롤백 / 정리

부분 롤백:

```bash
helm -n agentoe-staging rollback agentoe-backend
```

전체 정리 (staging 비용 절감용):

```bash
cd skeleton/deploy/helm && ENV=staging make uninstall

cd ../terraform/environments/staging
terraform destroy
# Atlas cluster 는 destroy 가 시간 오래 걸리고 백업 함께 사라짐 — destroy 전 PIT snapshot 확인.
```

> ⚠️ **state 파일은 destroy 한다고 사라지지 않음.** S3 / DynamoDB lock table 도 정리하려면 별도로 `bootstrap-state` 디렉토리에서 `terraform destroy` 또는 콘솔에서 수동.

---

## 10. 다음 단계 (production 으로 가기 전 체크)

- [ ] IRSA role 별 권한 최소화 검토 (현재는 staging-friendly 한 넓은 정책 일부 포함)
- [ ] WAFv2 ACL 생성 + ALB 연동 (`alb.ingress.kubernetes.io/wafv2-acl-arn`)
- [ ] ALB / NLB access log S3 버킷 + lifecycle (90d → Glacier)
- [ ] Prometheus 장기 보관 (Mimir / S3) — 기본 7d 만 retention
- [ ] backup: Mongo PIT + Velero (PV 백업) 검증
- [ ] `terraform destroy` 시뮬레이션을 별도 sandbox 계정에서 1 회 (의도치 않게 끌려 나가는 리소스 점검)
- [ ] DR drill: region failover 시뮬레이션 (Atlas multi-region, Redis Multi-AZ failover trigger)

---

## 부록 A — 환경 변수 한눈에

| 변수                          | 출처 (terraform output 키)         | 용도                                  |
|------------------------------|------------------------------------|---------------------------------------|
| `CLUSTER_NAME`               | `cluster_name`                     | kubeconfig + Karpenter / external-dns |
| `VPC_ID`                     | `vpc_id`                           | Karpenter NodeClass                   |
| `ALB_CONTROLLER_ROLE_ARN`    | `alb_controller_role_arn`          | ALB controller IRSA SA                |
| `EXTERNAL_DNS_ROLE_ARN`      | `external_dns_role_arn`            | external-dns IRSA SA                  |
| `ESO_ROLE_ARN`               | `eso_irsa_role_arn`                | external-secrets controller IRSA SA   |
| `ROUTE53_ZONE_ID`            | `route53_zone_id`                  | external-dns / cert-manager DNS01     |
| `ACME_EMAIL`                 | (수동)                              | Let's Encrypt 등록                    |

## 부록 B — 시간 예산 합계

| 단계                     | 예산  |
|--------------------------|-------|
| 0. 도구 / 자격 준비      | 10 분 |
| 1. state 백엔드          |  5 분 |
| 2. terraform apply       | 45 분 |
| 3. 클러스터 부트스트랩   | 18 분 |
| 4. 시크릿 적재           |  5 분 |
| 5. 이미지 빌드/푸시      | 15 분 |
| 6. helm install          |  8 분 |
| 7. 상태 점검             |  5 분 |
| **합계**                 | **약 1h 50m** |
