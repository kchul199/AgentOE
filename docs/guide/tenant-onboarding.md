# Guide: Tenant Onboarding Checklist

| 항목 | 값 |
|---|---|
| 작성일 | 2026-02-28 |
| 최종 점검일 | 2026-04-18 |
| 담당 | 플랫폼 매니저 + 세일즈 엔지니어 |
| 관련 코드 | `backend/scripts/tenant_admin.py`, `app/repositories/tenant_repository.py` |

신규 테넌트 하나를 프로덕션에 연결할 때 따라가는 단계별 체크리스트.
"기본 흐름만 통과하면 1.5~2 시간" 을 목표로 작성.

## 0. 준비

수집해야 할 정보:

* **법인/고객사 명칭**, 담당자 이메일/Slack.
* **예상 동시 세션 수** (피크) — 쿼터 설정에 사용.
* **사용할 LLM 모델 티어** — Llama-4 계열 vs. Llama-3.3 70B 계열. 기본은 scout + 3.3-70B fallback.
* **이관(transfer) 대상 상담원 번호/SIP URI** — 필요한 경우만.
* **IdP(Identity Provider)** — Auth0 / Keycloak / 자체 발급.
  테넌트 별 `iss` / `aud` claim 값 확인.

## 1. tenant_id 채번

**형식 고정**: `^t_[a-z0-9_]{1,32}$` (ADR-002 참조).
예: `t_acme`, `t_foo_co_2026`.

* 이미 존재하는지 확인:
  ```bash
  python scripts/tenant_admin.py get --tenant t_acme
  # 없으면 "not found"
  ```
* 충돌하면 `{brand}_{region}` 등으로 유일화.
* `tenant_id` 는 **불변**. 재발급 없음.

## 2. Mongo 테넌트 도큐먼트 생성

```bash
python scripts/tenant_admin.py create \
  --tenant t_acme \
  --display-name "ACME Corp" \
  --contact kim@acme.co \
  --plan standard
```

생성 결과 (`tenants` 컬렉션):
```json
{
  "_id": "t_acme",
  "display_name": "ACME Corp",
  "plan": "standard",
  "contact_email": "kim@acme.co",
  "created_at": "2026-04-18T...Z",
  "status": "onboarding",
  "jwt": {
    "issuer": "https://acme.auth0.com/",
    "audience": "agentoe"
  },
  "llm_daily_token_limit": 500000,
  "llm_daily_cost_limit_cents": 2500,
  "max_concurrent_sessions": 50,
  "features": { "transfer": false, "kill_switch": true }
}
```

**주의**:
* `llm_daily_*` 한도는 계약서 기준. 미정이면 `standard` 플랜 기본값 사용.
* 나중에 올릴 수는 있으나 **한번 올리면 내리기 어렵다** (고객 기대치 관성).

## 3. IdP 설정

### 3-1. Issuer / Audience 등록

테넌트 도큐먼트의 `jwt.issuer` 는 IdP 의 `iss` claim 과 **문자 단위로** 일치.
trailing slash 하나 차이로 401 남발 — 정답은 IdP 실제 응답의 `/.well-known/openid-configuration` 에서 복사.

### 3-2. 커스텀 claim

아래가 JWT payload 에 반드시 포함되어야 함:
* `tenant_id` — 서버가 JWT → tenant 를 매핑할 때 사용. **테넌트 간 격리의 최종 방어선**.
* `sub` — 사용자 식별 (audit log 용).
* `scope` — 선택적. 현재는 `scenarios:write`, `scenarios:publish` 등이 사용됨.

IdP 측에 "커스텀 claim rule" 또는 "Action" 으로 주입.

### 3-3. JWKS 공개

* `https://{idp}/.well-known/jwks.json` 이 공개 접근 가능한지 확인.
* Cache-Control 은 **짧게** (300s 이하) 설정 — [JWKS kid 회전 runbook](../runbook/jwks-kid-rotation.md) 참조.

## 4. 초기 시나리오 시드

플랫폼 매니저가 템플릿 시나리오 하나를 draft 로 심어두고, 테넌트 운영자가 이어서 편집하는 흐름.

```bash
python scripts/tenant_admin.py seed-scenario \
  --tenant t_acme \
  --template cs_default \
  --scenario-id cs_default_v1
```

템플릿에는 아래가 포함:
* `entry: greeting` 노드.
* `classify_intent` → 3~4 개 라벨로 분기하는 기본 구조.
* `fallback_node: handoff` — [Scenario Authoring](./scenario-authoring.md) 권장대로 반드시 지정.
* `limits.max_turns: 12` (표준값).

테넌트에게 전달:
* Builder UI URL + 로그인 방법.
* 시나리오 authoring 가이드 링크.
* "Publish 는 테스트 통화 후에 하세요" 안내.

## 5. 쿼터 · 리밋 설정

| 항목 | 경로 | 기본값 |
|---|---|---|
| 일일 토큰 한도 | `tenants.llm_daily_token_limit` | 500,000 |
| 일일 비용 한도 | `tenants.llm_daily_cost_limit_cents` | 2,500 (= $25) |
| 동시 세션 한도 | `tenants.max_concurrent_sessions` | 50 |
| 세션 당 최대 턴 | 시나리오 `limits.max_turns` | 20 |
| 세션 당 최대 원가 | 시나리오 `limits.max_cost_cents` | 10 |

**경계 상황**:
* 고객이 예상 피크의 1.2~1.5x 를 요청 → 승인.
* 2x 를 초과 → 세일즈가 한 번 더 확인 (비용 리스크).

## 6. Kill-switch 테스트

* 샌드박스 환경에서 [Kill-switch runbook](../runbook/kill-switch-ops.md) 의 절차로 tenant scope 활성화/해제를 한 번 실행 — 플레이북을 읽기만 하는 것이 아니라 실제로 키를 set/del 하여 응답 503 → 200 전환을 본다.
* 테넌트 담당자에게 "긴급 시 고객 지원 티켓으로 요청 → 플랫폼 온콜이 kill-switch 활성화" 정책을 공지.

## 7. 모니터링 · 알람

### 7-1. Prometheus 대시보드

* Grafana 의 "AgentOE – Tenant Overview" 대시보드에 `tenant` 변수로 `t_acme` 를 추가 (대부분 datasource variable 에 자동 반영).
* 참고 메트릭: [Prometheus 메트릭 카탈로그](../reference/prometheus-metrics.md).

### 7-2. 알람 규칙 (PrometheusRule)

테넌트 별로 다음을 복사 (`infra/prometheus/rules/tenant-{tenant}.yml`):

* **High error rate** — `sum(rate(agentoe_pipeline_calls_total{tenant="t_acme", status="error"}[5m])) / sum(rate(agentoe_pipeline_calls_total{tenant="t_acme"}[5m])) > 0.1`
* **Quota near limit** — `agentoe_llm_cost_cents_total{tenant="t_acme"} > (0.8 * <daily_limit>)`
* **DLQ depth** — `dlq_depth{tenant="t_acme"} > 20`

PrometheusRule manifest 적용:
```bash
kubectl apply -f infra/prometheus/rules/tenant-t_acme.yml
```

### 7-3. Slack

* 알람은 **#ops-{tenant}** 채널로. 없으면 CS가 채널 생성.
* `#ops-changes` — 공용 변경 채널 (kill-switch on/off, 쿼터 조정 등).
* `#scenario-changes` — 테넌트가 시나리오 publish 할 때 통지 ([Scenario Authoring](./scenario-authoring.md)).

## 8. 스모크 테스트

테넌트가 publish 하기 전, 플랫폼 운영자가 샘플 통화를 수행:

```bash
# 1) 샘플 JWT 발급 (IdP 의 Management API)
TOKEN=$(python scripts/issue_test_jwt.py --tenant t_acme)

# 2) WebSocket 세션 오픈
python scripts/smoke_call.py --tenant t_acme --token "$TOKEN" \
  --scenario cs_default_v1 --utterance "안녕하세요, 요금 문의입니다"
```

성공 기준:
* 파이프라인 latency P95 < 2000ms (시나리오 특성에 따라 조정).
* 로그에 `LLMQuotaExceeded`, `policy_block`, `circuit_breaker_open` 이 없음.
* TTS 출력이 음성으로 들림 (오디오 파일 확인).

## 9. 상태 전환

Mongo `tenants.status` 를 `onboarding` → `active` 로:
```bash
python scripts/tenant_admin.py activate --tenant t_acme
```

`active` 상태는 빌링 리포트에 포함되는 기준.

## 10. 인수인계 문서

테넌트에게 전달할 1-pager:

* 로그인 URL + 초기 계정 정보
* Builder UI 간단 튜토리얼 (스크린샷 5~6 장)
* 이 문서 ([Scenario Authoring](./scenario-authoring.md)) 링크
* 장애 시 고객이 우리에게 연락하는 경로 (이메일, Slack, On-call 번호)

Slack 에 온보딩 완료 메시지:
```
#ops-changes
테넌트 t_acme (ACME Corp) 가 active 로 전환되었습니다.
연락처: kim@acme.co
최초 쿼터: 500k tokens/day, $25/day
담당 PM: @alice
```

## 체크리스트 요약

- [ ] 0. 준비 정보 수집
- [ ] 1. `tenant_id` 채번 (패턴 `^t_[a-z0-9_]{1,32}$`)
- [ ] 2. Mongo `tenants` 도큐먼트 생성
- [ ] 3. IdP 설정 (iss/aud + custom claim + JWKS)
- [ ] 4. 초기 시나리오 seed (`cs_default` 템플릿)
- [ ] 5. 쿼터 / 동시 세션 한도 설정
- [ ] 6. Kill-switch 절차 리허설 (샌드박스에서 실제 on/off)
- [ ] 7. Prometheus / Grafana / Slack 알람 연결
- [ ] 8. 스모크 통화 테스트 통과
- [ ] 9. 상태 `active` 전환
- [ ] 10. 테넌트 인수인계 1-pager 전달

## 관련

* [ADR-002: 멀티테넌트 키 네임스페이스](../adr/ADR-002-tenant-key-namespace.md)
* [Guide: Scenario Authoring](./scenario-authoring.md)
* [Runbook: Kill-switch 운영](../runbook/kill-switch-ops.md)
* [Runbook: LLM Quota 초과 대응](../runbook/llm-quota-exceeded.md)
* [Reference: Prometheus 메트릭 카탈로그](../reference/prometheus-metrics.md)
