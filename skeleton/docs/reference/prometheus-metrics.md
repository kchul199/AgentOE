# Reference: Prometheus 메트릭 카탈로그

| 항목 | 값 |
|---|---|
| 작성일 | 2026-03-14 |
| 최종 점검일 | 2026-04-18 |
| 관련 코드 | `backend/app/core/metrics.py` |
| Endpoint | `GET /metrics` (Prometheus scrape), `GET /api/v1/status/metrics` (JSON) |

AgentOE 가 노출하는 모든 Prometheus 메트릭 목록과 PromQL 예시.
이 문서는 **구현된 메트릭만** 포함한다 — 구현 상태는 `backend/app/core/metrics.py` 를 기준으로 작성.

---

## 공통 주의사항

* **Prefix**: 모든 메트릭 이름은 `agentoe_` 로 시작.
* **멀티-Pod**: 각 Pod 가 독립적으로 `/metrics` 를 노출. Prometheus Operator 가 개별 수집.
  **전체 합계**는 항상 PromQL `sum(...) by (...)` 로 얻는다.
* **Pod 로컬 게이지**: `active_sessions` 는 Pod 재시작 시 0 으로 리셋되는 것이 정상.
  "전체 활성 세션" 은 `sum(agentoe_active_sessions) by (tenant)`.
* **레이블 카디널리티**: `tenant` 레이블은 카디널리티가 높을 수 있으므로, 대시보드에서 항상 `by (tenant)` 대신
  필요한 테넌트만 필터링 (`{tenant="t_acme"}`) 하는 것이 효율적.

---

## 파이프라인

### `agentoe_pipeline_calls_total`

| 속성 | 값 |
|---|---|
| Type | Counter |
| Labels | `tenant`, `status` |
| `status` 값 | `success` \| `error` \| `degraded` |

AI 파이프라인(STT → LLM → TTS) 1회 완료 시 1 증가.
`degraded` = 부분 실패(예: TTS 는 fallback 로 대체) 지만 통화 지속.

**PromQL**:
```promql
# 분당 호출 수
sum(rate(agentoe_pipeline_calls_total[1m])) by (tenant, status)

# 에러율 (5분 이동 평균)
sum(rate(agentoe_pipeline_calls_total{status="error"}[5m])) by (tenant)
  /
sum(rate(agentoe_pipeline_calls_total[5m])) by (tenant)
```

### `agentoe_pipeline_latency_ms`

| 속성 | 값 |
|---|---|
| Type | Histogram |
| Labels | `tenant` |
| Buckets | 50, 100, 200, 300, 500, 750, 1000, 1500, 2000, 3000, 5000 |

파이프라인 end-to-end latency (ms).

**PromQL**:
```promql
# P95
histogram_quantile(
  0.95,
  sum(rate(agentoe_pipeline_latency_ms_bucket[5m])) by (le, tenant)
)

# 2초 초과 비율
sum(rate(agentoe_pipeline_latency_ms_bucket{le="2000"}[5m])) by (tenant)
  /
sum(rate(agentoe_pipeline_latency_ms_count[5m])) by (tenant)
```

---

## STT / LLM / TTS 개별

동일한 구조로 세 개: `agentoe_stt_*`, `agentoe_llm_*`, `agentoe_tts_*`.

### `agentoe_{stt|llm|tts}_calls_total`

| Type | Counter |
| Labels | `tenant`, `status` |
| `status` | `success` \| `error` |

### `agentoe_{stt|llm|tts}_latency_ms`

| Type | Histogram |
| Labels | `tenant` |
| Buckets | 파이프라인과 동일 |

**권장 SLO (latency budget — `get_all_metrics` 응답에 포함)**:
* STT: 500ms P95
* LLM: 500ms P95 (first-token-latency 기준, streaming 전제)
* TTS: 300ms P95 (첫 청크)
* 합계: 2500ms P95

---

## 상담원 이관

### `agentoe_transfer_requests_total`

| Type | Counter |
| Labels | `tenant`, `reason` |
| `reason` 예시 | `user_request` \| `low_confidence` \| `policy_block` \| `tool_failure` \| `escalation` |

SIP REFER 가 송출된 시점에 1 증가 (성공/실패 여부는 여기에 담지 않음 — 실패는 [DLQ](../runbook/dlq-processing.md) 로).

**PromQL**:
```promql
# 사유별 분포 (5분)
sum(rate(agentoe_transfer_requests_total[5m])) by (tenant, reason)
```

---

## 정책 / 보안

### `agentoe_policy_blocks_total`

| Type | Counter |
| Labels | `tenant`, `level` |
| `level` 예시 | `prompt_injection` \| `toxicity` \| `pii_leak` \| `out_of_scope` |

PolicyGate 가 요청/응답을 차단했을 때.

---

## 세션 / Circuit Breaker

### `agentoe_active_sessions`

| Type | Gauge |
| Labels | `tenant` |

**이 Pod 의** 활성 WebSocket 세션 수. Pod 재시작 시 0 리셋.
전체 집계는 `sum(agentoe_active_sessions) by (tenant)`.

### `agentoe_circuit_breaker_state`

| Type | Gauge |
| Labels | `service` |
| 값 | `0` = CLOSED, `1` = HALF_OPEN, `2` = OPEN |

`service` 예: `groq_llm`, `groq_stt`, `gcp_tts`, `crm_lookup`, ...

**PromQL**:
```promql
# 현재 OPEN 인 서비스
agentoe_circuit_breaker_state == 2

# 최근 10분 내 OPEN 진입 횟수
changes(agentoe_circuit_breaker_state[10m]) >= 1
```

---

## LLM 쿼터 / 사용량 (Track 3)

### `agentoe_llm_quota_checks_total`

| Type | Counter |
| Labels | `tenant`, `scope`, `result` |
| `scope` | `tokens` \| `cost` \| `none` |
| `result` | `ok` \| `warn` \| `fallback` \| `reject` |

한 번의 LLM 호출 직전 쿼터 enforcer 가 판정한 결과.
정상(`ok`) 이면 `scope="none"`.
한도 초과면 어떤 축(`tokens`/`cost`) 에서 걸렸는지를 기록.

**PromQL** — 쿼터 reject 발생 시 알람:
```promql
sum(increase(agentoe_llm_quota_checks_total{result="reject"}[5m])) by (tenant) > 0
```

→ [LLM Quota runbook](../runbook/llm-quota-exceeded.md) 연결.

### `agentoe_llm_tokens_consumed_total`

| Type | Counter |
| Labels | `tenant`, `model` |

실제 LLM 호출이 소비한 토큰 수 (input + output 합산).

### `agentoe_llm_cost_cents_total`

| Type | Counter |
| Labels | `tenant`, `model` |

실제 LLM 호출 비용 (센트 단위).
```promql
# 오늘 테넌트별 누적 비용 (달러)
sum(
  increase(agentoe_llm_cost_cents_total[24h])
) by (tenant) / 100
```

---

## JWKS 캐시 (Track 3)

### `agentoe_jwks_lookups_total`

| Type | Counter |
| Labels | `result` |
| `result` | `hit` \| `miss` \| `force_refresh` \| `fail` |

**테넌트 레이블 없음** — JWKS 캐시는 프로세스 전역.

**PromQL** — miss 비율이 평소 대비 급증 시 [JWKS 회전 runbook](../runbook/jwks-kid-rotation.md):
```promql
sum(rate(agentoe_jwks_lookups_total{result="miss"}[5m]))
  /
sum(rate(agentoe_jwks_lookups_total[5m]))
```

### `agentoe_jwks_refresh_duration_seconds`

| Type | Histogram |
| Labels | `result` |
| `result` | `success` \| `failure` |
| Buckets | 0.05, 0.1, 0.25, 0.5, 1.0, 2.0, 5.0, 10.0 (초) |

원격 JWKS fetch 1회의 소요 시간.
**`failure` 샘플이 발생하면** IdP 또는 네트워크에 문제 — 30초 백오프 진입.

---

## WebSocket Back-pressure (Track 2 P2)

느린 클라이언트가 서버 송신 버퍼를 잠식해 다른 세션의 메모리를 말아먹지 못하도록,
세션당 송신 큐(`BoundedWSSender`) 를 두고 초과분을 drop 한다. 이 지표들은 그 drop 이
**어디서 얼마나** 일어나는지 가시화한다.

### `agentoe_ws_send_queue_depth`

| 속성 | 값 |
|---|---|
| Type | Gauge |
| Labels | `tenant` |

현재 시점에 **이 Pod** 의 모든 세션 송신 큐의 최신 점유율. enqueue/drain 때마다 갱신된다.
Pod-로컬 값이므로 전체 보기에는 `sum by (tenant)`.

`max_queue_size` 기본값 = 64 (≈ 3.2초 분량 audio). 이 값에 근접하면 해당 테넌트의
네트워크 상태가 나쁘거나 Pod 내에 느린 세션이 누적되고 있다는 뜻.

**PromQL**:
```promql
# 테넌트별 현재 최대 큐 점유
max(agentoe_ws_send_queue_depth) by (tenant)

# 5분간 평균 점유율이 50% 초과한 테넌트
avg_over_time(agentoe_ws_send_queue_depth[5m]) > 32
```

### `agentoe_ws_drops_total`

| 속성 | 값 |
|---|---|
| Type | Counter |
| Labels | `tenant`, `kind` |
| `kind` 값 | `audio` \| `event` \| `full` |

큐 overflow 로 실제로 drop 된 이벤트 누적 수. `kind` 는 drop 정책을 구분한다:

* `audio` — 오디오(`tts_ready`) 로 큐가 가득 찼을 때, **가장 오래된 오디오 1개** 를 제거하고 새 이벤트 채택 (drop-oldest).
  실시간성이 중요한 오디오는 "최근" 이 가치. 오래된 청크 drop 은 UX 손실이 적다.
* `event` — 비-오디오 이벤트(state 변경/텍스트 등) 가 가득 찬 큐에 들어오려 했을 때, **이번 이벤트를 버린다** (drop-newest).
  상태 전이와 텍스트는 순서 보존이 중요해 기존 큐를 유지.
* `full` — 드물게, 큐에 drop 대상 오디오가 없는데 새 오디오가 들어온 경계 케이스.

**PromQL**:
```promql
# 초당 drop 비율 (테넌트별)
sum(rate(agentoe_ws_drops_total[1m])) by (tenant, kind)

# 5분간 총 drop 이 임계치를 넘은 테넌트 (알람 기준)
sum(increase(agentoe_ws_drops_total[5m])) by (tenant) > 500
```

**경보 권장**: `kind="audio"` drop 이 분당 100개를 지속적으로 초과하면 — 특정 테넌트의
네트워크가 불안정하거나 클라이언트 버전 이슈. 지원팀 에스컬레이션 대상.

---

## 메트릭 카탈로그 요약

| 이름 | Type | 레이블 | 어디서 쓰는가 |
|---|---|---|---|
| `agentoe_pipeline_calls_total` | Counter | tenant, status | 호출량·에러율 |
| `agentoe_pipeline_latency_ms` | Histogram | tenant | P95/P99 latency |
| `agentoe_stt_calls_total` | Counter | tenant, status | STT 상태 |
| `agentoe_stt_latency_ms` | Histogram | tenant | STT latency |
| `agentoe_llm_calls_total` | Counter | tenant, status | LLM 상태 |
| `agentoe_llm_latency_ms` | Histogram | tenant | LLM latency |
| `agentoe_tts_calls_total` | Counter | tenant, status | TTS 상태 |
| `agentoe_tts_latency_ms` | Histogram | tenant | TTS latency |
| `agentoe_transfer_requests_total` | Counter | tenant, reason | 이관 빈도·사유 |
| `agentoe_policy_blocks_total` | Counter | tenant, level | 정책 차단 |
| `agentoe_active_sessions` | Gauge | tenant | 현재 세션 수 (Pod 로컬) |
| `agentoe_circuit_breaker_state` | Gauge | service | CB 상태 |
| `agentoe_llm_quota_checks_total` | Counter | tenant, scope, result | 쿼터 초과 탐지 |
| `agentoe_llm_tokens_consumed_total` | Counter | tenant, model | 토큰 빌링 |
| `agentoe_llm_cost_cents_total` | Counter | tenant, model | 비용 빌링 |
| `agentoe_jwks_lookups_total` | Counter | result | JWKS 캐시 건전성 |
| `agentoe_jwks_refresh_duration_seconds` | Histogram | result | JWKS fetch 시간 |
| `agentoe_ws_send_queue_depth` | Gauge | tenant | WS 송신 큐 점유율 (Pod 로컬) |
| `agentoe_ws_drops_total` | Counter | tenant, kind | WS back-pressure drop 수 |

### DLQ Recording Rule

메트릭은 아니지만 참조: Prometheus recording rule `dlq_depth` 가 Redis exporter 결과를 테넌트별로 재집계.
→ `prometheus/recording_rules.yml` 에 정의. [DLQ runbook](../runbook/dlq-processing.md) 의 알람 기준.

---

## JSON API

Prometheus scrape 와는 별도로 `GET /api/v1/status/metrics` 가 in-process `_store` 기반 JSON 을 반환.
Prometheus 가 없는 개발 환경이나, 대시보드에서 단일 Pod 스냅샷이 필요한 경우 사용.

응답 예:
```json
{
  "timestamp": 1745052000.123,
  "pipeline": {
    "t_acme": {
      "calls": {"total": 1234, "success": 1200, "error": 30, "degraded": 4},
      "error_rate": 0.0243,
      "pipeline_latency_ms": {
        "p50": 820, "p95": 1820, "p99": 2480,
        "avg": 912.4, "max": 3420, "count": 1234, "sum": 1125480
      },
      ...
    }
  },
  "transfers": { "t_acme": { "user_request": 8, "low_confidence": 2 } },
  "active_sessions": { "t_acme": 4 },
  "circuit_breakers": [
    {"service": "groq_llm", "state": "CLOSED", "failures": 0, ...}
  ],
  "latency_budget_ms": {"stt": 500, "llm": 500, "tts": 300, "total": 2500}
}
```

* `pipeline_latency_ms.p50/p95/p99` 는 **슬라이딩 윈도우 (최근 1000 샘플)** 기반.
* `avg/count/sum` 은 서비스 시작 이후 전체 누적 — `avg = sum / count` 가 항상 성립.

---

## 대시보드 권장 패널

"AgentOE – Tenant Overview" 기본 레이아웃:

1. **상단 KPI**: 호출 수 / 에러율 / P95 latency / 활성 세션 (각각 `by (tenant)` 집계).
2. **Latency heatmap**: `agentoe_pipeline_latency_ms_bucket` — 시간대별 bucket 분포.
3. **Stage breakdown**: STT/LLM/TTS latency p95 를 같은 패널에 겹쳐 — 병목 식별.
4. **Quota burndown**: 오늘 누적 토큰 / 비용 / 한도 대비 비율 (`max_over_time` + limit join).
5. **CB / DLQ 상태**: CB 현재 상태 + `dlq_depth` 시계열.
6. **Transfer / Policy 이벤트**: 사유별 분포 (stacked area).

---

## 관련

* [Runbook: LLM Quota 초과 대응](../runbook/llm-quota-exceeded.md)
* [Runbook: JWKS kid 회전](../runbook/jwks-kid-rotation.md)
* [Runbook: DLQ 처리 절차](../runbook/dlq-processing.md)
* [Runbook: Kill-switch 운영](../runbook/kill-switch-ops.md)
* [Guide: Tenant Onboarding Checklist](../guide/tenant-onboarding.md)
