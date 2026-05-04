# Runbook — Production Cutover Gates

> 적용: prod 환경에 traffic 을 받기 전 통과해야 할 게이트.
> staging 검증만으로 prod 안전 보장 불가 — 추가 게이트 필요.

## 0. 게이트 흐름

```text
staging 안정 (14일 burn rate < 1.0)
      ↓
[Gate A] Load test (k6) — 정상 + 1.5× peak
      ↓
[Gate B] Chaos drill 1회 (pod-kill / network-loss)
      ↓
[Gate C] DR drill — staging Velero 복원 1회
      ↓
[Gate D] Security — Trivy CVE 0건, WAFv2 active, image cosign 서명 확인
      ↓
[Gate E] On-call 준비 — PagerDuty rotation, runbook 숙지, status page
      ↓
[Gate F] 영업 공지 — 핵심 고객에 cutover 일정/리스크 사전 공유
      ↓
prod cutover Stage A (vbgw 10% canary)
```

각 게이트는 **명시적 통과 기록 필요**. owner 가 PR 또는 issue 에 ✅ 댓글.

## Gate A — Load test

### 목표
SLO 임계 (`docs/reference/slo.md`) 가 정상 + 1.5× 트래픽에서 유지.

### 시나리오 (k6)

```javascript
// scripts/loadtest/k6-baseline.js (TODO 작성)
// 200 concurrent calls, 30분 sustained
// 분당 setup 100 / 분당 destroy 100 = steady state 200
// p95 e2e latency, setup ratio, drop ratio 모두 SLO target 만족
```

### Pass 조건

| 메트릭 | SLO target | Load test 기간 | Pass 조건 |
|--------|-----------|---------------|-----------|
| call_setup_ratio | ≥ 99.9% | 30m + 30m peak | 99.5% 이상 |
| pipeline_latency p95 | ≤ 2.5s | 동일 | ≤ 3s |
| mid_call_drop | < 0.1% | 동일 | < 0.3% |
| Pod CPU usage | — | peak | < 80% (HPA headroom) |
| Mongo Atlas CPU | — | peak | < 70% |
| Redis CPU | — | peak | < 60% |

### Fail 시
- HPA max 늘리기
- Atlas 인스턴스 사이즈 한 단계 위 (M30 → M40)
- Redis node_type 위로 (r7g.large → r7g.xlarge)
- 다시 Gate A.

## Gate B — Chaos drill

### 시나리오 (chaos-mesh)

1. **PodChaos: pod-kill** — agentoe-backend Pod 1개 무작위 종료. 다른 Pod 가 통화 받는지 검증.
2. **NetworkChaos: delay** — backend ↔ Mongo 사이 100ms latency 1분간 주입. CB / 폴백 동작 검증.
3. **NetworkChaos: loss** — 5% 패킷 손실. 통화 setup 영향 측정.

### Pass 조건
- 모든 시나리오에서 **고객 perceivable 끊김 없음** (active 통화 종료까지 정상 처리)
- alertmanager 가 정상 알람 발송 (silence 안 됨)
- 5분 내 자동 복구

### Fail 시
- preStop drain timing / terminationGracePeriodSeconds 조정
- Circuit breaker threshold 조정
- 다시 Gate B.

## Gate C — DR drill

### 시나리오
- staging 환경에서 `terraform destroy -target=module.eks` 후 `apply` + Velero 복원.
- 측정: RTO / 사람 시간 / 자동화 비율.

### Pass 조건
- RTO ≤ 60분 (`docs/runbook/disaster-recovery.md` §0)
- 복원 후 smoke gRPC client 5건 모두 OK
- Mongo 데이터 손실 없음 (사전 dump 와 정확히 일치)

### Fail 시
- Velero schedule frequency 늘림
- 복원 자동화 추가 (helm post-restore hook)
- 다시 Gate C.

## Gate D — Security

### Trivy
```bash
# 모든 prod 이미지 검증 — CRITICAL/HIGH 0건
for svc in backend frontend vbgw-ai vbgw-bridge vbgw-orchestrator; do
  trivy image $ECR_REGISTRY/agentoe-prod/$svc:vX.Y.Z \
    --severity CRITICAL,HIGH --ignore-unfixed --exit-code 1
done
```

### WAFv2
```bash
# ALB 의 wafv2-acl-arn annotation 확인
kubectl -n agentoe get ingress agentoe-backend -o jsonpath='{.metadata.annotations}' \
  | jq -r '."alb.ingress.kubernetes.io/wafv2-acl-arn"'
# → 비어 있으면 fail. WAFv2 ACL 별도 생성 + 변수 등록 필요.
```

### Cosign (옵션 — 향후)
```bash
cosign verify --key ~/.cosign.pub $ECR_REGISTRY/agentoe-prod/backend:vX.Y.Z
```

### Pass 조건
- Trivy CVE CRITICAL/HIGH 0건
- WAFv2 ACL ALB 에 attach 확인
- (옵션) cosign verify OK

## Gate E — On-call 준비

체크리스트:

- [ ] PagerDuty schedule — 24/7 rotation 활성, primary + secondary
- [ ] Slack `#ops-incident` 가입 — 모든 on-call 멤버
- [ ] runbook 숙지 — `alert-response-*.md`, `vbgw-ai-cutover.md`, `disaster-recovery.md`
- [ ] kubectl prod context 접속 권한 — VPN 연결 후 정상 동작
- [ ] AWS Console 접속 권한 — billing dashboard, CloudWatch logs
- [ ] Status page 게시 권한 — incident 발생 시 5분 안에 update 가능
- [ ] 사고 보고 채널 — CTO / 영업 리더 연락처

## Gate F — 영업 / 고객 공지

- [ ] cutover 일정 사전 공지 (최소 1주 전)
- [ ] 핵심 고객 (LTV 상위 10%) 직접 통보
- [ ] 응답 톤/지연 변화 가능성 명시 (vbgw-ai vs AgentOE backend 의 차이)
- [ ] 비상 연락처 공유

## prod cutover 진행 (게이트 모두 통과 후)

`docs/runbook/vbgw-ai-cutover.md` Stage B-D 그대로 진행:

```text
Stage B — prod 10% canary (24h bake)
      ↓ SLO 게이트 5종 통과
Stage C — prod 50% canary (12h bake)
      ↓ 동일 게이트
Stage D — prod 100% promote
      ↓ 24h 안정 후
Stage E — vbgw-ai deprecate (1주 후)
```

## 게이트 실패 시 — 사후

게이트 실패는 **prod 차단** 이지 사고 아님. retro 권장:

1. 어느 게이트, 어느 메트릭이 fail 했나?
2. 사전에 측정했어야 했는가? (있었다면 모니터링 보강)
3. 다음 cutover 시도 전 추가 작업 list (이 runbook 의 게이트 자체 갱신 가능)

## 관련 문서

- `docs/reference/slo.md` — SLO 임계
- `docs/runbook/vbgw-ai-cutover.md` — 4 stage 절차
- `docs/runbook/disaster-recovery.md` — RTO/RPO + DR 절차
- `docs/guide/ci-cd.md` — Variables/Secrets / branch protection
