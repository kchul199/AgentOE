# Runbook — `APIErrorBudgetBurn_*`

> 트리거 alert: `APIErrorBudgetBurn_FastBurn`, `APIErrorBudgetBurn_SlowBurn`, `APIErrorBudgetBurn_Ticket`
> 대시보드: [API SLO](https://grafana.agentoe.io/d/api-slo)
> 의미: backend `/api/*` 5xx 비율이 SLO 99.9% 의 budget 을 burn rate ≥ 6× 또는 14.4× 로 소진 중.

## 0. 즉시 첫 5분 (오너 = on-call)

1. Slack `#ops-incident` 에 단일 thread 시작:
   ```
   :rotating_light: API burn — owner=@me, IM=open, sev=page, link=<grafana>
   ```
2. Grafana `api-slo` 대시보드 → "Top routes by error rate" 패널에서 가장 큰 기여 route 확인.
3. 최근 30분 내 배포가 있었나? `helm -n agentoe history agentoe-backend | head` — 직전 revision 의 시각이 burn 시작점과 일치하면 **8.A 롤백**.

## 1. 진단

### 1.1 로그 — error 메시지 패턴
```bash
kubectl -n agentoe logs -l app.kubernetes.io/name=agentoe-backend \
  --since=15m --tail=500 \
  | jq -r 'select(.level == "ERROR") | "\(.timestamp) \(.path) \(.error // .exception // .msg)"' \
  | sort | uniq -c | sort -nr | head -20
```

가장 빈도 높은 에러 메시지를 채굴 → 다음 분기.

### 1.2 외부 의존성 그래프
- **Mongo / Redis** — `agentic` 대시보드의 "Circuit breaker" 패널, OPEN 상태인 서비스가 있으면 그 의존성으로 점프 (8.C/D)
- **JWKS** — `infra` 대시보드 "JWKS refresh ratio" — 99% 미만이면 8.E
- **Groq / Google** — backend log 에 외부 API 5xx / timeout 다발이면 8.F (LLM/STT/TTS 폴백 모드 고려)

### 1.3 트래픽 스파이크 vs 정상 트래픽
```promql
# 동일 시간대 평균 대비 현재 RPS
sum(rate(http_requests_total{job="agentoe-backend"}[5m]))
/
avg_over_time(sum(rate(http_requests_total{job="agentoe-backend"}[5m]))[7d:1h])
```
- 결과 > 3 → **DDoS 또는 마케팅 캠페인** 가능 → 8.G (rate-limit 강화 + autoscale 조정)
- 결과 ≈ 1 → 트래픽 정상, 에러 자체가 문제

### 1.4 노드 건강
```bash
kubectl top nodes
kubectl get nodes -o wide
```
NotReady 노드 / OOM 다발 → infra 문제.

## 2. 완화 결정 트리

```text
직전 배포가 원인?
├── YES → 8.A (rollback) → 5분 안에 SLO 회복 확인
└── NO
    ├── 외부 의존성 OPEN? → 그 의존성 runbook 으로 점프
    ├── 트래픽 스파이크? → 8.G (rate-limit / scale)
    └── 그 외 → 8.B (kill-switch degraded mode 검토) + 코드 hot-fix
```

## 3. 종료 기준

- `slo:http_request_success_ratio:rate5m` ≥ 99.9% 가 **연속 15분** 유지
- Alertmanager 가 자동 resolve 발송
- Slack thread 에 다음 메시지:
  ```
  :white_check_mark: API burn resolved — root cause=<one-liner>, postmortem=<gh-issue-url-if-needed>
  ```

## 4. 사후

- **page** 였으면 **postmortem 필수** (`engineering:incident-response` skill 사용)
- **ticket** 만 떴으면 다음 영업시간에 회고 안건으로 추가

## 5. 관련 문서

- SLO 정의: `docs/reference/slo.md`
- Kill-switch 운영: `docs/runbook/kill-switch-ops.md`
- LLM quota 폴백: `docs/runbook/llm-quota-exceeded.md`
- Redis outage: `docs/runbook/redis-outage.md`
- JWKS rotation: `docs/runbook/jwks-kid-rotation.md`

---

## 8. 완화 액션 (Cookbook)

### 8.A — 직전 배포 롤백
```bash
helm -n agentoe history agentoe-backend
helm -n agentoe rollback agentoe-backend <REV>
kubectl -n agentoe rollout status deploy/agentoe-backend
```
GitHub Actions 에서 직전 안전한 태그로 재배포:
```bash
gh workflow run deploy-production.yml -f image_tag=v1.4.0 -f skip_canary=true
```

### 8.B — Kill-switch (degraded voice / agentic 비활성)
```bash
# Agentic 만 비활성, 룰 기반 폴백
kubectl -n agentoe set env deploy/agentoe-backend AGENTIC_DISABLED=true
# 또는 kill-switch runbook 참고
```

### 8.C — Redis 일시 정지 후 재연결 강제
- `docs/runbook/redis-outage.md` 의 5번 단계 — backend Pod 재시작으로 connection pool 새로고침.

### 8.D — Mongo 연결 풀 고갈
- HPA 보다 Pod 수가 부족할 수 있음. 임시 scale-up:
  ```bash
  kubectl -n agentoe scale deploy/agentoe-backend --replicas=$(kubectl -n agentoe get deploy agentoe-backend -o jsonpath='{.status.replicas}' | awk '{print $1*2}')
  ```

### 8.E — JWKS 회전 / 캐시 강제 새로고침
- `docs/runbook/jwks-kid-rotation.md`

### 8.F — 외부 LLM/STT 폴백
- `agentic` 대시보드의 "Circuit breaker" → 해당 서비스가 OPEN 상태가 되어 자동 폴백 작동 중인지 확인
- 안 되어 있으면 코드 폴백 경로 점검 (PR 시급)

### 8.G — 트래픽 스파이크 대응
- HPA max replicas 임시 상향:
  ```bash
  kubectl -n agentoe patch hpa agentoe-backend --type merge -p '{"spec":{"maxReplicas":100}}'
  ```
- Rate-limit 강화 (테넌트 한도 일시 하향):
  ```bash
  kubectl -n agentoe set env deploy/agentoe-backend RATE_LIMIT_PER_TENANT_PER_MIN=2000
  ```
