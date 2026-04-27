# Reference — SLO / SLI / Error Budget

> 본 문서는 **Service Level Objectives** 의 단일 진실 소스입니다. PrometheusRule, Grafana 대시보드, 알람 라우팅, canary 메트릭 게이트는 모두 여기 정의를 따릅니다.

## 1. 우리가 약속하는 것 (요약)

| 서비스           | 약속                                            | 목표  | 측정 윈도우 |
|------------------|-------------------------------------------------|-------|-------------|
| `backend-api`    | `/api/*` HTTP 요청 성공률 (5xx ≠ 클라이언트 오류) | 99.9% | 30일 rolling |
| `backend-api`    | `/api/*` HTTP 요청 지연 P95 ≤ 500ms             | 99%   | 30일 rolling |
| `agentic`        | 파이프라인 호출 성공률 (success+degraded 통과)   | 99.5% | 30일 rolling |
| `agentic`        | 파이프라인 E2E 지연 P95 ≤ 2.5s                  | 95%   | 30일 rolling |
| `vbgw`           | 통화 setup 성공률                                | 99.9% | 30일 rolling |
| `vbgw`           | 통화 중 끊김 (mid-call drop) 발생률              | < 0.1% | 30일 rolling |
| `auth`           | JWKS refresh 성공률                              | 99.9% | 30일 rolling |

원칙: SLO 는 **고객이 느끼는 것** 만 측정한다. 내부 지표 (CPU 사용률, GC 시간 등) 는 SLI 가 아니라 진단 metric.

## 2. SLI 정의 (PromQL)

### 2.1 backend-api · request success ratio

```promql
# Good = 5xx 가 아닌 응답 (4xx 는 클라 책임 — SLO 분자에서 제외하지 않음)
sum(rate(http_requests_total{job="agentoe-backend",status!~"5.."}[5m]))
  /
sum(rate(http_requests_total{job="agentoe-backend"}[5m]))
```

> `http_requests_total` 라벨: `method`, `route`, `status`. 라벨 카디널리티를 막기 위해 path 는 router-level template (`/api/v1/sessions/:id`) 로 정규화한다 — Phase 3-E 미들웨어 책임.

### 2.2 backend-api · request latency

```promql
# fraction of requests served under 500ms
sum(rate(http_request_duration_seconds_bucket{job="agentoe-backend",le="0.5"}[5m]))
  /
sum(rate(http_request_duration_seconds_count{job="agentoe-backend"}[5m]))
```

배제: `route="/api/v1/livez|readyz|metrics/prometheus"` 는 운영 트래픽 — SLO 에서 제외.

### 2.3 agentic · pipeline success

```promql
# success + degraded 둘 다 "고객은 결국 응답을 받음" 으로 간주.
# error 만 실패.
sum(rate(agentoe_pipeline_calls_total{status=~"success|degraded"}[5m]))
  /
sum(rate(agentoe_pipeline_calls_total[5m]))
```

### 2.4 agentic · E2E latency

```promql
sum(rate(agentoe_pipeline_latency_ms_bucket{le="2500"}[5m]))
  /
sum(rate(agentoe_pipeline_latency_ms_count[5m]))
```

> 버킷 정의: `[50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000]` (ms). 2500ms SLO 는 2000 / 3000 사이 — 보간 없이 안전하게 3000 버킷으로 카운트하면 약간 보수적, 그래서 우리는 `le="2500"` 가 아니라 정확히 `le="3000"` 의 ratio 를 90% 이상 유지하도록 alerting rule 에서 보강.

### 2.5 vbgw · call setup success

```promql
# vbgw 가 노출하는 agentoe_call_setup_total{result=ok|fail}
sum(rate(agentoe_call_setup_total{result="ok"}[5m]))
  /
sum(rate(agentoe_call_setup_total[5m]))
```

### 2.6 vbgw · mid-call drop rate

```promql
# drop = 통화 시작 후 30s 내 비정상 종료
sum(rate(agentoe_call_terminations_total{reason=~"network|server_error|crash"}[5m]))
  /
sum(rate(agentoe_call_setup_total{result="ok"}[5m]))
```

### 2.7 auth · JWKS refresh success

```promql
sum(rate(agentoe_jwks_refresh_duration_seconds_count{result="success"}[5m]))
  /
sum(rate(agentoe_jwks_refresh_duration_seconds_count[5m]))
```

## 3. Error Budget

| SLO target | 30일 허용 실패율 | 30일 다운타임/실패 시간 (예) |
|-----------|------------------|------------------------------|
| 99.0%      | 1.00%            | 7시간 12분                   |
| 99.5%      | 0.50%            | 3시간 36분                   |
| 99.9%      | 0.10%            | 43분 12초                    |
| 99.95%     | 0.05%            | 21분 36초                    |

**Error budget = (1 − SLO) × total events in window.** 지연 SLO 의 경우 "허용된 슬로우 요청 수" 로 환산.

### 3.1 burn rate

`burn_rate = (실패율) / (1 − SLO)`

- burn rate = 1.0 → 정확히 30일 동안 budget 을 모두 소진
- burn rate = 14.4 → 30일 budget 을 50시간 만에 소진
- burn rate = 36 → 30일 budget 을 20시간 만에 소진

### 3.2 multi-window multi-burn-rate alert (SRE 권장 기본)

| Severity | 단기 윈도우 / 임계 | 장기 윈도우 / 임계 | 의미                                        |
|----------|--------------------|--------------------|---------------------------------------------|
| **page** (즉시) | 5m, burn ≥ 14.4   | 1h, burn ≥ 14.4   | 2% budget / 1h — 페이지콜                   |
| **page** (느림) | 30m, burn ≥ 6.0   | 6h, burn ≥ 6.0    | 5% budget / 6h — 페이지콜                   |
| **ticket**      | 2h, burn ≥ 1.0    | 24h, burn ≥ 1.0   | 시간당 정상치 이상 — 다음 영업시간 확인     |
| **ticket**      | 6h, burn ≥ 1.0    | 3d, burn ≥ 1.0    | 누적 추세 — 회고 안건                       |

이 두 윈도우 AND 조건이 PrometheusRule 에 그대로 인코딩됩니다 (Phase 3-B).

## 4. Error budget policy

> "예산이 남아 있을 땐 마음껏 배포한다. 다 쓰면 멈춘다."

### 4.1 정상 운영 (burn rate ≤ 1.0, 24h)

- 자유롭게 배포
- 신규 기능 PR 자유롭게 머지
- canary 단계는 5분 bake 만으로 충분

### 4.2 위험 (page 발화 또는 burn rate > 6.0, 6h)

- **즉시 진행 중인 신규 기능 배포 보류.** 단, 알람 자체를 줄이는 fix 는 제외.
- on-call 이 incident channel 개설.
- 운영팀 ↔ 개발팀 daily standup 까지 회의.

### 4.3 budget 소진 (월 예산의 80% 이상 소진)

- **freeze**: 신규 기능 머지 차단 (lint/security fix 만 허용).
- 모든 PR 에 `slo-budget-low` 라벨 자동 부착, 머지 게이트 추가.
- 회복 우선 actions:
  1. 가장 큰 burn 원인 분석 → 해결 PR 우선
  2. SLO 자체가 너무 빡빡한지 검토 — 단 분기당 1회 이내로만 조정
  3. 의존성 (Atlas / Redis / Groq / Google) 의 SLA 점검

### 4.4 회복

- 30일 윈도우에서 burn rate < 1.0 가 7일 연속 → freeze 해제
- 4.3 에서 한 결정에 대한 retro 진행

## 5. SLO 측정 윈도우 — 30일 vs 7일

- **alerting** 는 짧은 multi-window (5m/1h, 30m/6h) — 빠른 반응
- **error budget tracking** 는 30일 rolling — 공정한 평가
- **monthly review** 는 calendar month — stakeholder 보고

PrometheusRule 의 recording rule 이 두 윈도우 시리즈를 모두 미리 계산한다.

## 6. 외부 의존성 SLA → 우리 SLO 영향

| 의존성             | 외부 SLA  | 영향 가정 (월)                  | 완화                                       |
|--------------------|-----------|--------------------------------|--------------------------------------------|
| MongoDB Atlas M30  | 99.995%   | 21초/월 다운                    | retryWrites=true + connection pool         |
| ElastiCache Redis  | 99.99% (Multi-AZ) | 4분/월 failover           | Sentinel auto reconnect (redis-py)         |
| Groq STT/LLM       | 99.5% 공식    | 3시간 36분/월                  | CB → 폴백 LLM (Bedrock Claude)             |
| Google STT/TTS     | 99.9%     | 43분/월                         | CB → kill_switch.degraded_voice            |
| Auth0 / Okta JWKS  | 99.99%    | 4분/월                          | jwks_cache 30분 TTL + 1회 force_refresh    |

요약: **외부 의존성만으로도 4시간/월 영향 가능.** 99.9% backend SLO 는 외부 사고에 보수적이려면 **CB + 폴백** 필수.

## 7. SLO 변경 절차

1. SRE 또는 PM 이 SLO 변경 PR (이 문서 + PrometheusRule 동시 수정)
2. 변경 근거 — 30일 데이터, 고객 임팩트, 비즈니스 컨텍스트
3. CTO + Product 검토 (월간 리뷰 회의 안건)
4. 승인 후 머지 — 변경 시점부터 budget 새 윈도우 시작 (기존 budget 은 보존, 회고에만 사용)

## 8. 측정 도구 매핑

| SLO              | Recording rule (Phase 3-B 파일)              | Grafana 패널                    | Alert (severity) |
|------------------|-----------------------------------------------|----------------------------------|------------------|
| api success      | `slo:http_request_success_ratio:rate5m`       | API SLO 대시보드 / 패널 1        | page / ticket    |
| api latency      | `slo:http_latency_under_500ms:ratio5m`        | API SLO 대시보드 / 패널 2        | page / ticket    |
| pipeline success | `slo:agentic_success_ratio:rate5m`            | Agentic 대시보드 / 패널 1        | page / ticket    |
| pipeline latency | `slo:agentic_latency_under_2_5s:ratio5m`      | Agentic 대시보드 / 패널 2        | ticket           |
| call setup       | `slo:vbgw_call_setup_ratio:rate5m`            | VBGW 대시보드 / 패널 1           | page             |
| mid-call drop    | `slo:vbgw_mid_call_drop_ratio:rate5m`         | VBGW 대시보드 / 패널 2           | page             |
| jwks refresh     | `slo:auth_jwks_refresh_ratio:rate5m`          | Infra 대시보드 / 패널 4          | ticket           |

## 9. 카나리 게이트와의 관계

`deploy-production.yml` canary 단계는 5분 bake. 이 동안 **새로 배포된 Pod 의 error rate 가 기준 SLO 의 burn rate > 6.0 을 넘으면 자동 롤백**:

```promql
# canary release 의 error rate
sum(rate(http_requests_total{job="agentoe-backend",release="agentoe-backend-canary",status=~"5.."}[5m]))
  /
sum(rate(http_requests_total{job="agentoe-backend",release="agentoe-backend-canary"}[5m]))
> (1 - 0.999) * 6.0   # = 0.006 = 0.6%
```

현재 GHA 워크플로는 단순 로그 grep 기반 (Prometheus 의존성 없는 안전판). 본 SLO PromQL 게이트로 업그레이드하는 것은 Phase 3 다음 작업.

## 10. 참고

- Google SRE Book Ch 4 — Service Level Objectives
- "Implementing SLOs" (Google SRE Workbook Ch 2)
- Sloth (https://sloth.dev) — SLO YAML 을 PrometheusRule 로 generate. 추후 도입 검토.
