# Runbook — Disaster Recovery (DR)

> 적용: prod 환경 한정. staging 은 destroy/rebuild 로 대응.
> 목표: 다중 장애 시나리오 → 측정 가능한 RTO/RPO 안에 복구.

## 0. RTO / RPO 정의

| 시나리오                              | RTO 목표 | RPO 목표 | 비고                                                                |
|---------------------------------------|----------|----------|---------------------------------------------------------------------|
| Pod 1개 OOM / restart                 | < 30s    | 0        | K8s 자동 재시작 — DR 아님                                           |
| Node 1대 손실 (AZ 일부)                | < 2분    | 0        | EKS managed NG + topologySpreadConstraints                          |
| AZ 1개 완전 손실                       | < 5분    | 0        | per-AZ NAT + 3-AZ Mongo Atlas + ElastiCache Multi-AZ failover       |
| Atlas cluster crash (region 내)        | < 15분   | < 5초    | Atlas PIT 자동 failover                                             |
| **EKS 클러스터 손실** (terraform destroy 등) | **< 60분** | **0** (manifests Velero) / **< 1h** (EBS snapshot) | Velero 복원 + Mongo Atlas 무관 |
| **Region 손실 (ap-northeast-2)**        | **< 4h** | **< 30분** | dr_region (ap-northeast-1) 으로 cold-DR. Atlas 가 multi-region 면 < 30분 |
| Atlas org 손실 (계정 침해 등)          | < 24h    | < 4h     | M30 weekly snapshot S3 archive — 별도 backup IAM                    |

## 1. 시나리오 별 절차

### 1.1 AZ 1개 손실

K8s 가 자동 처리 — 모니터링만:

```bash
kubectl get nodes -o wide                   # NotReady AZ 확인
kubectl get pods -A -o wide | grep -v Running | head
# → topologySpreadConstraints 가 다른 AZ 로 재배치. 확인 후 추가 액션 X.

# Atlas 측 — automatic failover 가 다른 AZ 의 secondary 를 primary 로 promote.
# API: GET /v2/clusters/{name}/replicaSet 으로 primary 변경 확인.

# ElastiCache Multi-AZ — 같은 패턴으로 자동.
```

페이지콜이 없으면 정상 처리됨. SLO 알람이 오면 §1.2.

### 1.2 EKS 클러스터 복구 (Velero 사용)

대규모 사고 — terraform destroy 잘못 / API server 영구 장애 등.

```bash
# 1) prod 네임스페이스 선언 — 새 클러스터 만들고 Velero 부터 복구
cd deploy/terraform/environments/prod
terraform apply                              # ~45분 소요

aws eks update-kubeconfig \
  --name "$(terraform output -raw cluster_name)" \
  --region ap-northeast-2 \
  --alias agentoe-prod

# 2) Velero 부터 설치 (다른 모든 것 전에)
cd deploy/k8s-bootstrap
helm repo add vmware-tanzu https://vmware-tanzu.github.io/helm-charts
helm -n velero upgrade --install velero vmware-tanzu/velero --version 7.2.1 \
  --create-namespace \
  -f values/velero.values.yaml \
  --set serviceAccount.server.annotations."eks\.amazonaws\.com/role-arn"="$(cd ../terraform/environments/prod && terraform output -raw velero_role_arn)" \
  --set configuration.backupStorageLocation[0].bucket="$(cd ../terraform/environments/prod && terraform output -raw velero_backup_bucket)"

# 3) 가장 최근 backup 검색
velero get backups | head -5
velero describe backup <NAME>

# 4) 복원 (cluster-scope 부터, 그 다음 namespace)
velero restore create cluster-scope-restore \
  --from-backup <NAME> \
  --include-cluster-resources=true \
  --include-namespaces=cert-manager,external-secrets,monitoring \
  --restore-volumes=false   # cluster-scope 는 PV 없음

# 시크릿 동기화 대기
kubectl -n external-secrets wait --for=condition=Ready pod -l app.kubernetes.io/name=external-secrets --timeout=5m

# 5) workload 복원
velero restore create app-restore \
  --from-backup <NAME> \
  --include-namespaces=agentoe,vbgw \
  --restore-volumes=true

kubectl -n agentoe rollout status deploy/agentoe-backend --timeout=10m
kubectl -n vbgw rollout status deploy/vbgw-bridge --timeout=10m

# 6) Mongo / Redis 도달성 검증
POD=$(kubectl -n agentoe get pod -l app.kubernetes.io/name=agentoe-backend -o name | head -1)
kubectl -n agentoe exec "$POD" -- python3 -c "
import os, motor.motor_asyncio, redis.asyncio as r, asyncio
async def main():
    cli = motor.motor_asyncio.AsyncIOMotorClient(os.environ['MONGODB_URI'])
    print('mongo →', await cli.admin.command('ping'))
    rc = r.from_url(os.environ['REDIS_URL'])
    print('redis →', await rc.ping())
asyncio.run(main())
"

# 7) ALB / external-dns 가 hostname 다시 부여 (1-2 분)
# 8) smoke + Slack #ops-incident 에 복구 완료 게시
```

**예상 RTO**: terraform 45분 + Velero restore 10분 = ~ **60분**.

### 1.3 Atlas 의 PIT 복원 (특정 시점으로 되돌리기)

데이터 corruption / 잘못된 마이그레이션 / 악성 쿼리 등.

```bash
# Atlas UI:
#   Project → Backup → Continuous → Restore To Point in Time
#   → 시점 선택 (UTC) → 새 cluster 또는 같은 cluster 의 collection 선택
#   → 30분 ~ 2시간 소요 (cluster 사이즈 비례)

# 또는 atlas CLI:
atlas backups restores start \
  --type pointInTime \
  --pointInTimeUTCSeconds <SECONDS> \
  --clusterName agentoe-prod \
  --targetClusterName agentoe-prod-restored \
  --projectId $ATLAS_PROJECT_ID

# 새 cluster 로 복원했을 때:
#   1. tfvars 의 atlas connection string 갱신 후 backend env 변경
#   2. Helm upgrade 로 backend 재시작
#   3. 검증 후 옛 cluster 삭제 (또는 삭제 보류 — 추가 검증 시간 확보)
```

**RTO**: Atlas 복원 30분 ~ 2h. **RPO**: < 5초 (PIT 의 oplog tail).

### 1.4 Region 손실 (ap-northeast-2 전체)

가장 큰 사고. 사전 준비 + 사람 결정 필요.

#### 1.4.1 사전 조건 (이미 갖춰져야 함)

- [ ] `dr_region=ap-northeast-1` (Tokyo) 으로 terraform variable 설정
- [ ] (옵션) `atlas_dr_region_enabled=true` — Atlas multi-region read replica
- [ ] Velero backup 이 dr_region S3 로 cross-region replicate (옵션 — `aws_s3_bucket_replication_configuration`)
- [ ] terraform module 들이 region-agnostic (provider alias 사용)

#### 1.4.2 cold-DR 절차

```bash
# 1) DR region 에 prod stack 신규 생성
export AWS_REGION=ap-northeast-1   # ★ DR
cd deploy/terraform/environments/prod-dr   # 별도 backend.tf + tfvars (TODO)
terraform init
terraform apply                            # ~45분

# 2) Velero S3 가 cross-region replication 되어 있으면 dr region 의 bucket 자동 사용
#    아니면 main region S3 가 살아 있을 때 미리 dump 한 backup 이 필요

# 3) §1.2 의 EKS 복구 절차 그대로 (단지 region 이 다름)

# 4) DNS 갱신 — Route53 health check 가 자동 failover 설정되어 있으면 자동.
#    아니면 수동:
aws route53 change-resource-record-sets ... \
  --change-batch '{"Changes":[{"Action":"UPSERT","ResourceRecordSet":{
    "Name":"api.agentoe.io","Type":"CNAME","TTL":60,
    "ResourceRecords":[{"Value":"<DR-ALB-DNS>"}]}}]}'

# 5) Atlas — multi-region 이면 자동 failover. 아니면 새 region 에 cluster 신규 생성 후
#    snapshot 복원 → connection string 변경.
```

**RTO**: 약 **3-4 시간** (Atlas 복원 시간이 dominant). multi-region 이면 < 30분.

### 1.5 운영팀 / 영업 / 고객 커뮤니케이션

- **Status page** — 사고 시작 5분 안에 게시 (`https://status.agentoe.io`)
- **Slack** `#ops-incident` 에 owner / IM 매 30분 update
- **고객 통보** — RTO 가 1h 초과 예상 시 영업/CSM 이 핵심 고객 직접 연락
- **사후** — 24h 안에 blameless postmortem (`engineering:incident-response` skill)

## 2. 분기별 DR drill

매 분기 (Mar/Jun/Sep/Dec) 첫 주 토요일 04-08 KST 에:

### 2.1 단순 drill (Q1, Q3)

- AZ 1개 cordon + drain → 자동 복구 검증 (§1.1)
- Velero restore 검증 — staging 에서만, prod 무관
- Atlas PIT 복원 — 별도 prod-restore-test cluster 만들고 복원 후 즉시 삭제

### 2.2 완전 drill (Q2, Q4)

- staging 환경 destroy 후 Velero 만으로 복구
- 측정: RTO / 사람 시간 / 자동화 비율
- 이슈 발견 시 다음 분기 backlog

### 2.3 drill 결과 기록

`docs/reports/dr-drill-YYYY-Q[1-4].md` 형식으로 보관.

## 3. 권장 자동화 (TODO)

다음은 미구현 — 운영 안정 후 도입 권장:

| 자동화                         | 효과                              | 우선순위 |
|--------------------------------|-----------------------------------|----------|
| Velero S3 bucket cross-region replication | region 손실 시 backup 도 안전       | High     |
| Route53 health check + failover record   | DNS 자동 전환                       | High     |
| Atlas multi-region cluster (electable in DR) | RPO < 30s, RTO < 30분            | Medium   |
| Regular restore drill (월 1회 자동)          | "복원 안 된 backup" 위험 차단        | High     |
| `terraform plan` weekly drift 검증           | 의도 외 변경 감지                   | Medium   |
| KMS key 다중 region 정책                    | encrypted backup 도 다른 region 에서 복호화 | Medium   |

## 4. 관련 문서

- `docs/reference/slo.md` — SLO 임계 (DR 복구 후 SLO 달성 검증)
- `docs/runbook/staging-bringup.md` — staging 0→1 부트스트랩 (prod 도 거의 동일)
- `docs/runbook/alert-response-*.md` — 단발 인시던트 대응
- prod terraform: `deploy/terraform/environments/prod/`
- Velero: `deploy/k8s-bootstrap/{values/velero.values.yaml,manifests/velero-schedules.yaml}`
