# Runbook: Kill-switch 운영

| 항목 | 값 |
|---|---|
| 작성일 | 2026-02-20 |
| 최종 점검일 | 2026-04-18 |
| 대상 온콜 | platform-oncall, 플랫폼 매니저 |
| 관련 코드 | `app/domain/kill_switch.py`, `app/middleware/kill_switch_middleware.py` |

## Kill-switch 가 무엇을 차단하는가

Kill-switch 는 **신규 요청** 을 서비스 레벨에서 즉시 거부하는 안전 장치다.
이미 진행 중인 통화(WebSocket 세션)는 **영향받지 않는다**.

두 가지 범위가 있다:

| Key | 범위 | 효과 |
|---|---|---|
| `killswitch:global` | 전역 | 모든 새 요청 503 반환, health 는 200 유지 |
| `killswitch:tenant:{tenant_id}` | 단일 테넌트 | 해당 테넌트의 새 요청만 503 |

둘이 동시에 설정돼 있으면 둘 다 적용 (뭐가 먼저 매치되어도 결과는 차단).

## 언제 사용하는가

사용 사례를 명확히 구분해둔다:

* **즉시 사용 OK**:
  * 외부 LLM API 에서 요금 폭증(예: 10분 만에 월 한도 80% 도달) 감지 시 글로벌 활성화.
  * 특정 테넌트 악용/오남용(무한 루프 시나리오, DDoS 패턴) 감지 시 tenant scope 활성화.
  * 데이터 손상 가능성이 있는 배포 직후 긴급 롤백 시 브리지.
* **사용 금지**:
  * 일상적 점검 윈도우 — 별도 maintenance 모드 사용 (미구현, TODO).
  * 버그 회피용 장기 켜두기 — 반드시 티켓 + 해제 기한 설정.

## 활성화

### 전역

```bash
kubectl -n data exec redis-0 -- redis-cli SET killswitch:global "1"
# 선택적으로 이유 기록
kubectl -n data exec redis-0 -- redis-cli SET killswitch:global:reason "LLM cost spike 2026-04-18"
```

### 특정 테넌트

```bash
kubectl -n data exec redis-0 -- redis-cli SET killswitch:tenant:t_acme "1"
kubectl -n data exec redis-0 -- redis-cli SET killswitch:tenant:t_acme:reason "abuse triage 2026-04-18"
```

**확인**:
```bash
kubectl -n agentoe exec deploy/backend-api -- \
  curl -s http://localhost:8000/api/v1/health | jq '.kill_switch'
```

## 해제

```bash
kubectl -n data exec redis-0 -- redis-cli DEL killswitch:global
# 또는
kubectl -n data exec redis-0 -- redis-cli DEL killswitch:tenant:t_acme
```

활성화/해제 **모두 Slack #ops-changes 채널에 로그** 남길 것.

## Redis 장애 중 kill-switch

Redis 가 완전 장애라면 kill-switch 키를 조회 자체가 불가능하다.
이 경우 두 가지 옵션:

1. **`KILL_SWITCH_FALLBACK` env var**
   * `global` → Redis 를 읽지 못하면 기본적으로 "활성화" 로 간주 (fail-closed).
   * `none` (기본값) → Redis 를 읽지 못하면 "비활성화" 로 간주, 서비스 계속 (fail-open).
   * `kubectl -n agentoe set env deploy/backend-api KILL_SWITCH_FALLBACK=global`
2. **Deployment 스케일 제로**
   * `kubectl -n agentoe scale deploy/backend-api --replicas=0`
   * 가장 확실하지만 복귀에 시간 소요. 소요 시간 감안하여 결정.

## 테스트

### 샌드박스 환경

```bash
# ON
kubectl --context=staging -n data exec redis-0 -- redis-cli SET killswitch:tenant:t_demo "1"
curl -H "X-Tenant-Id: t_demo" https://api.staging.agentoe.../api/v1/scenarios
# → 503, body 에 retry_after

# OFF
kubectl --context=staging -n data exec redis-0 -- redis-cli DEL killswitch:tenant:t_demo
curl -H "X-Tenant-Id: t_demo" https://api.staging.agentoe.../api/v1/scenarios
# → 200
```

### CI smoke

`tests/integration/test_kill_switch.py` — middleware 가 Redis 키 존재 시 503 을 돌려주는지 검증.

## 응답 형식

```json
HTTP/1.1 503 Service Unavailable
Retry-After: 60

{
  "code": "KILL_SWITCH_ACTIVE",
  "scope": "global" | "tenant",
  "reason": "LLM cost spike 2026-04-18",
  "message": "service temporarily unavailable"
}
```

`reason` 필드는 운영팀이 설정해둔 사유 — 고객 support 가 즉시 답변 가능하도록.

## 관련

* [Runbook: Redis 장애 대응](./redis-outage.md)
* [Runbook: LLM Quota 초과 대응](./llm-quota-exceeded.md) — quota 초과와 kill-switch 는 비슷해보이지만 층위가 다름.
