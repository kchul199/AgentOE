# Runbook: LLM Quota 초과 대응

| 항목 | 값 |
|---|---|
| 작성일 | 2026-03-02 |
| 최종 점검일 | 2026-04-18 |
| 대상 온콜 | platform-oncall, 계약 관리자 |
| 관련 코드 | `app/core/quota.py`, `app/services/llm_service.py` |

## 쿼터 구조

AgentOE 는 테넌트별 **일일 토큰 / 비용 한도** 를 Redis 카운터로 관리한다.

| Redis 키 | 의미 |
|---|---|
| `quota:{tenant}:tokens:{YYYY-MM-DD}` | 하루 총 토큰 소비량 |
| `quota:{tenant}:cost_cents:{YYYY-MM-DD}` | 하루 비용 (센트 단위) |

한도값은 테넌트 설정(`settings.yaml` 또는 Mongo `tenants` 컬렉션)의
`llm_daily_token_limit`, `llm_daily_cost_limit_cents` 에서 읽는다.

매 LLM 호출 전:

1. 현재 카운터 값을 `GET`.
2. 예상 토큰/비용을 더했을 때 한도 초과면 **`LLMQuotaExceeded` 예외** 발생 → 시나리오 fallback 분기.
3. 응답 수신 후 실제 사용량을 `INCRBY`.

## 탐지

* Metric: `agentoe_llm_quota_checks_total{result="blocked"}` rate > 0
* Metric: `agentoe_llm_cost_cents_total` 가 해당 테넌트 임계치에 수렴
* Slack: 고객사 CS 가 "봇이 대답을 안 해요" 제보
* Application log: `llm_quota_exceeded` 이벤트 (구조화 로그)

## 즉시 대응

### A. 사용량이 실제 과다한지 확인

```bash
# 오늘 토큰 소비
redis-cli GET quota:t_acme:tokens:$(date -u +%Y-%m-%d)

# 역사적 추이 (최근 7일)
for d in $(seq 0 6); do
  D=$(date -u -d "-$d days" +%Y-%m-%d)
  echo -n "$D  tokens="
  redis-cli GET quota:t_acme:tokens:$D
done
```

### B. 원인 분류

* **정상 사용인데 한도가 낮음** (고객사 통화량 증가, 계약 갱신 필요):
  1. CS/세일즈 팀에 통지.
  2. 임시 override 로 한도 확장 (아래).
* **비정상 사용** (특정 세션/시나리오가 반복 루프):
  1. 로그에서 `scenario_id` 분포 확인: `jq 'select(.event=="llm.response") | .scenario_id' < logs | sort | uniq -c`.
  2. 특정 시나리오가 지배적이면 해당 시나리오의 `max_turns` / `temperature` 재설정을 고객사에 권고.
  3. 악용 의심 시: [Kill-switch 운영](./kill-switch-ops.md) 으로 테넌트 차단.

### C. 임시 한도 override

긴급 상황에서 한도를 늘리려면:

```bash
# 방법 1: 카운터 자체를 초기화 (권장하지 않음 — 감사 기록 사라짐)
redis-cli DEL quota:t_acme:tokens:$(date -u +%Y-%m-%d)

# 방법 2: Mongo 의 테넌트 설정 직접 수정 (권장)
#   backend/scripts/tenant_admin.py 를 사용
python scripts/tenant_admin.py set-limit \
  --tenant t_acme --kind tokens --value 2000000 --reason "peak day override 2026-04-18"
```

**방법 1 은 블레임리스 포스트모템에 반드시 언급할 것**.
방법 2 는 감사 로그가 남는다.

## 장기 조치

* 쿼터가 자주 걸리는 테넌트 → 세일즈에 티켓, 플랜 업그레이드 제안.
* 특정 시나리오가 자주 초과 → 시나리오 작성 가이드([Scenario Authoring](../guide/scenario-authoring.md)) 에
  `max_turns` / `max_tokens` 권고치를 추가.
* 월 1회 리포트: 테넌트 × 월별 토큰/비용 트렌드 (PromQL 또는 Grafana).

## 클라이언트 관점 응답

```json
HTTP/1.1 429 Too Many Requests

{
  "code": "LLM_QUOTA_EXCEEDED",
  "scope": "tokens" | "cost_cents",
  "limit": 1000000,
  "consumed": 1000512,
  "reset_at": "2026-04-19T00:00:00Z",
  "message": "daily LLM token limit reached; resets 00:00 UTC"
}
```

**시나리오 fallback 은 별도 분기** — 즉, 시나리오에 `fallback_node` 가 지정되어 있으면 통화는 끊기지 않고 fallback 노드로 이동한다. 시나리오 작성 시 fallback 은 필수.

## 테스트

`tests/integration/test_quota.py` 가 다음을 커버:

* 정상 누적 — `INCRBY` 후 `GET`.
* 한도 초과 시 `LLMQuotaExceeded` 발생.
* Redis 다운 시 quota check skip (가용성 우선).

## 관련

* [Runbook: Kill-switch 운영](./kill-switch-ops.md)
* [Reference: Prometheus 메트릭 카탈로그](../reference/prometheus-metrics.md)
* [Guide: Scenario Authoring](../guide/scenario-authoring.md)
