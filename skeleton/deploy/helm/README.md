# agentoe Helm charts

3 개의 차트로 구성:

| 차트                | 책임                                  | 노출        |
|---------------------|---------------------------------------|-------------|
| `agentoe-backend`   | FastAPI / WebSocket / REST API        | ALB Ingress |
| `agentoe-vbgw`      | Voice Bridge — gRPC + WS, 통화 처리   | NLB (옵션)  |
| `agentoe-frontend`  | React SPA (nginx 컨테이너)            | ALB Ingress |

환경별 override 는 `values/<env>/*.values.yaml`. 공통 클러스터 리소스(PriorityClass 등)는 `values/shared/`.

## 워크플로

```bash
# 1) Terraform output 으로 REPLACE_* 토큰 채우기
terraform -chdir=../terraform/environments/staging output -json > /tmp/agentoe-tf-staging.json
ENV=staging make render-values

# 2) Lint / dry-run
ENV=staging make lint
ENV=staging make template | less

# 3) 배포 (PriorityClass + 3 chart)
ENV=staging make deploy-all

# 4) 상태 확인
ENV=staging make status

# 5) 롤백
ENV=staging make rollback-backend
```

## 환경

| ENV       | NAMESPACE          | 도메인                                    |
|-----------|--------------------|-------------------------------------------|
| staging   | agentoe-staging    | api-staging.agentoe.io / app-staging…     |
| prod      | agentoe            | api.agentoe.io / app.agentoe.io           |

## 시크릿 흐름

```
AWS Secrets Manager  ──ESO ClusterSecretStore──►  K8s Secret
   ${env}/redis_auth_token                 agentoe-{env}-secrets
   ${env}/mongo_app_password
   ${env}/google_application_credentials
   ${env}/jwt_secret
   ${env}/groq_api_key

Pod envFrom: [ConfigMap(${name}-config), Secret(${name}-secrets)]
   → REDIS_URL / MONGODB_URI 는 컨테이너 env 에서 $(VAR) 합성
```

## ALB 그룹 공유

`api.agentoe.io` (backend) 와 `app.agentoe.io` (frontend) 는 같은 ALB 1개 공유:

- annotation `alb.ingress.kubernetes.io/group.name: agentoe-{env}-public`
- backend.order=100, frontend.order=200

ALB Controller 가 두 Ingress 를 합쳐 단일 LB + 호스트 라우팅 룰 생성.

## 검증 체크리스트

배포 후 반드시 확인:

- `helm status -n <ns>` ─ DEPLOYED 상태
- `kubectl rollout status deployment/...` ─ 모든 deployment OK
- ExternalSecret SyncedSecrets 카운트 일치
- ALB ADDRESS 가 부여되고 ACM TLS 정상
- `/api/v1/livez`, `/api/v1/readyz` 200
- ServiceMonitor → Prometheus 타깃 UP
- HPA `kubectl get hpa` ─ TARGETS 가 `<unknown>` 아닌 % 값
- PDB MIN_AVAILABLE 충족 가능한지 (replicas ≥ minAvailable)
