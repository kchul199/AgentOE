# Runbook — vbgw-ai → AgentOE backend cutover

> 목적: vbgw bridge 가 호출하는 gRPC AI endpoint 를 vbgw-ai (Go, go-openai 직접) 에서 **AgentOE backend** (multi-tenant, agentic, scenarios) 로 전환.
> 적용 시점: Phase Y (backend 가 VoicebotAiService 구현) + Phase Z (vbgw chart 가 canary 지원) 모두 완료.
> 원자성: 양 프로젝트 PR 이 둘 다 머지된 후 진행. 한쪽만 머지된 상태 절대 금지.

## 0. 사전 조건 점검 (preflight)

```bash
# A) AgentOE backend 가 staging 에 배포되어 있고 grpc 노출 중인지
kubectl -n agentoe-staging get deploy agentoe-backend
kubectl -n agentoe-staging get svc agentoe-backend -o jsonpath='{.spec.ports[*].name}'
# → "http grpc" 둘 다 보여야 함

# B) backend 의 gRPC health 확인
kubectl -n agentoe-staging port-forward svc/agentoe-backend 50051:50051 &
grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check
# → {"status":"SERVING"}
kill %1

# C) vbgw_v2 의 chart 에 canary 블록 있는지
helm -n vbgw-staging template ./charts/vbgw -f canary-values.yaml \
  | grep -E "vbgw.io/track|AI_GRPC_ADDR" | head -10
# → stable + canary 두 블록, 각자 다른 AI_GRPC_ADDR

# D) 트래픽 측정 베이스라인 — 직전 1h 의 통화 수 / 에러율 / 지연 p95 기록
#    (Grafana → Agentic 대시보드, Phase 3-D 의 슬라이드 캡처)
```

| 항목                             | 값 (사전 기록)        |
|----------------------------------|-----------------------|
| 직전 1h 통화 수                  | __________           |
| 통화 setup 성공률 (1h)           | __________           |
| pipeline E2E p95                 | __________           |
| LLM 비용 평균/통화               | __________ (회귀 비교용) |

## 1. Stage A — staging 에서 100% canary (15분)

**목표**: staging 환경에서 backend 가 실제 통화를 처리할 수 있는지 검증. staging 은 위험 낮으니 canary 단계 생략 (100% backend).

```bash
# 1.1 vbgw_v2 측 — bridge values.yaml 의 grpcAiAddr 를 staging backend 로 변경
cat > /tmp/vbgw-staging-cutover.values.yaml <<'EOF'
bridge:
  replicaCount: 2
  grpcAiAddr: "agentoe-backend.agentoe-staging.svc.cluster.local:50051"
  grpcAiTLS: false
  canary:
    enabled: false
EOF

helm -n vbgw-staging upgrade vbgw ./charts/vbgw \
  -f charts/vbgw/values-staging.yaml \
  -f /tmp/vbgw-staging-cutover.values.yaml \
  --wait --timeout 5m

# 1.2 합성 통화 5건 (scripts 의 SIPP / pjsua 등)
cd vbgw_v2 && ./scripts/synthetic-call.sh --count 5 --staging

# 1.3 backend 로그 — VoicebotAiService.StreamSession 실제 트래픽 확인
kubectl -n agentoe-staging logs -l app.kubernetes.io/name=agentoe-backend --since=5m \
  | jq -r 'select(.event=="StreamSession_started" or .event=="pipeline_done") | .session_id' \
  | sort -u | wc -l
# → 5 (또는 그 이상)

# 1.4 SLO 시리즈 발화 확인
kubectl -n agentoe-staging exec deploy/agentoe-backend -- \
  curl -s localhost:8000/api/v1/metrics/prometheus \
  | grep -E 'agentoe_(grpc_sessions_total|call_setup_total|call_terminations_total)'
# → 카운터가 오름. setup_total{result="ok"} ≈ 5
```

**Pass 조건:**
- 합성 통화 5건 모두 setup ok
- pipeline_done 이벤트 5건
- mid-call drop 0
- p95 latency ≤ 베이스라인 × 1.2

**Fail 시:** §6 롤백.

## 2. Stage B — prod 에서 10% canary (24h bake)

```bash
# 2.1 deploy/helm/vbgw — prod values 에 canary 활성
cat > /tmp/vbgw-prod-canary-10pct.values.yaml <<'EOF'
bridge:
  replicaCount: 9                            # 90%
  grpcAiAddr: "ai-service:50051"             # 기존 vbgw-ai
  canary:
    enabled: true
    replicaCount: 1                          # 10% (=1/10)
    grpcAiAddr: "agentoe-backend.agentoe.svc.cluster.local:50051"
    grpcAiTLS: false
EOF

helm -n vbgw upgrade vbgw ./charts/vbgw \
  -f charts/vbgw/values-prod.yaml \
  -f /tmp/vbgw-prod-canary-10pct.values.yaml \
  --wait --timeout 5m

# 2.2 두 ReplicaSet 모두 ready 인지
kubectl -n vbgw get pods -l app.kubernetes.io/name=bridge \
  -L vbgw.io/track --sort-by=.spec.nodeName
# → stable 9개, canary 1개
```

**24h 모니터링 항목** (Grafana 에서 매 4h 체크):

| 게이트                                                       | 임계         | 위반 시       |
|--------------------------------------------------------------|--------------|---------------|
| backend 통화 setup 성공률 (15m)                              | ≥ 99.0%      | 즉시 §6 롤백 |
| backend pipeline E2E p95 (15m)                               | ≤ 3000ms     | 즉시 §6 롤백 |
| backend mid-call drop ratio (1h)                             | ≤ 0.5%       | 4h 안에 검토 |
| LLM 비용/통화 — backend vs vbgw-ai 차이 (1h 평균)             | ≤ +50%       | 8h 안에 검토 |
| Pod CrashLoop / OOM (전체 24h)                                | 0건          | 즉시 §6 롤백 |

게이트 위반 없으면 24h 후 §3 진행.

## 3. Stage C — prod 50% canary (12h bake)

```bash
cat > /tmp/vbgw-prod-canary-50pct.values.yaml <<'EOF'
bridge:
  replicaCount: 5                          # 50%
  grpcAiAddr: "ai-service:50051"
  canary:
    enabled: true
    replicaCount: 5                        # 50%
    grpcAiAddr: "agentoe-backend.agentoe.svc.cluster.local:50051"
EOF

helm -n vbgw upgrade vbgw ./charts/vbgw \
  -f charts/vbgw/values-prod.yaml \
  -f /tmp/vbgw-prod-canary-50pct.values.yaml \
  --wait --timeout 5m
```

§2 와 동일 게이트, 12h bake.

## 4. Stage D — prod 100% promote

```bash
cat > /tmp/vbgw-prod-promote.values.yaml <<'EOF'
bridge:
  replicaCount: 10                                 # 모두 backend
  grpcAiAddr: "agentoe-backend.agentoe.svc.cluster.local:50051"
  canary:
    enabled: false                                 # canary deployment 제거
EOF

helm -n vbgw upgrade vbgw ./charts/vbgw \
  -f charts/vbgw/values-prod.yaml \
  -f /tmp/vbgw-prod-promote.values.yaml \
  --wait --timeout 5m

# canary deployment 가 정리됐는지
kubectl -n vbgw get deploy -l app.kubernetes.io/component=bridge
# → vbgw-bridge (replicas=10) 만 보여야 함
```

**24h 안정 모니터링 후** §5 진행.

## 5. Stage E — vbgw-ai 정리 (선택, 안정 1주 후)

```bash
# vbgw-ai deployment 제거 (chart 자체에 vbgw-ai 가 있다면 values 로 비활성)
helm -n vbgw upgrade vbgw ./charts/vbgw \
  -f charts/vbgw/values-prod.yaml \
  --set vbgwAi.enabled=false \
  --wait --timeout 5m

# Service 도 정리
kubectl -n vbgw delete svc ai-service --ignore-not-found

# ECR 의 vbgw-ai 이미지는 보관 (롤백 옵션). Lifecycle policy 가 6개월 후 자동 회수.
```

vbgw_v2 측 PR 로 vbgw-ai 디렉토리 삭제는 **3개월 후** (충분한 베이크 후).

## 6. 롤백 (긴급 — 어느 단계에서든 가능)

### 6.1 즉시 (1분 내) — bridge env 만 되돌림

```bash
# canary disable + grpcAiAddr 원복
helm -n <vbgw-ns> upgrade vbgw ./charts/vbgw \
  -f charts/vbgw/values-<env>.yaml \
  --set bridge.grpcAiAddr=ai-service:50051 \
  --set bridge.canary.enabled=false \
  --wait --timeout 3m

# 진행 중 통화는 그대로 종료 (gRPC stream 자연 종료) — 새 통화만 vbgw-ai 로.
```

### 6.2 backend 측 차단 (1분 내, 더 강력)

bridge 가 endpoint 못 받으면 자동 폴백 동작 안 하므로, backend 측에서 gRPC port 자체를 끄면 connection refused → 신규 통화 모두 끊김. 이건 **6.1 보다 위험**, 진행 중 통화도 끊김. 6.1 가 권장.

```bash
kubectl -n agentoe set env deploy/agentoe-backend GRPC_ENABLED=false
```

### 6.3 사후

- Slack `#ops-incident` 에 thread 시작
- root cause 분석 → §6 롤백 결정 시점부터 24h 안에 retro
- 다음 cutover 시도 전 게이트 추가 (특정 메트릭 추가 측정 등)

## 7. 메트릭 — backend vs vbgw-ai 비교 시 보는 것

```promql
# backend 트래픽 (canary)
sum(rate(agentoe_grpc_sessions_total[5m]))

# vbgw-ai 트래픽 (stable)
# vbgw-ai 가 노출하는 메트릭에 따라 다름. 없으면 bridge 측 BRIDGE_TRACK 라벨 활용.

# bridge 측 (track 별 분리)
sum by (bridge_track) (rate(vbgw_grpc_calls_total[5m]))

# 비용 비교 — backend 쪽 LLM 사용량
sum by (model) (increase(agentoe_llm_cost_cents_total[1h]))
```

## 8. 알려진 차이 — backend 가 vbgw-ai 와 다른 점

| 동작                  | vbgw-ai (현재)              | AgentOE backend (cutover 후)              |
|-----------------------|------------------------------|--------------------------------------------|
| LLM provider          | go-openai (단일)             | router (Groq → Bedrock 폴백, 모델 선택)    |
| Multi-tenant          | 없음 (모든 통화 동일 처리)   | tenant_id 별 quota / scenarios / policy   |
| 시나리오              | 하드코딩                     | MongoDB 의 versioned scenario             |
| Token quota           | 없음                         | tenant 일일 quota + 4단계 정책 (ok/warn/fallback/reject) |
| Audit log             | 일반 로그만                  | WORM audit collection                      |
| JWT 검증              | 헤더 raw 사용                | JWKS 캐시 + kid 회전                       |
| Idempotency           | 없음                         | header 기반 + Redis TTL                    |
| Barge-in              | bridge 측에서만               | bridge + backend (clear_buffer signal)    |

cutover 시 영업/CSM 에 미리 공지: 통화 응답 톤·길이·레이턴시 가 약간 변할 수 있음.

## 9. 참고 문서

- `docs/guide/cross-project-integration.md` — 두 프로젝트 책임 분담
- `docs/runbook/grpc-stream-debug.md` — gRPC 흐름 디버깅
- `docs/reference/slo.md` §2.5/2.6 — vbgw 통화 SLO
- vbgw_v2 측: `docs/cutover-vbgw-ai-to-backend.md` (병행 작성 권장)
