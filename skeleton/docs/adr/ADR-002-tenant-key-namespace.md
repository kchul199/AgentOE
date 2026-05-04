# ADR-002: 멀티테넌트 키 네임스페이스 설계

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 작성일 | 2026-01-24 |
| 최종 점검일 | 2026-04-18 |
| 관련 코드 | `app/core/redis_client.py`, `app/core/quota.py`, `app/repositories/scenario_repository.py` |

## 맥락

AgentOE 는 단일 백엔드 배포(cluster 공유) + 단일 데이터 스토어 세트(Redis, MongoDB)로
**수십~수백 개 테넌트** 를 서비스한다.
서로 다른 테넌트의 데이터가 **탐지되지 않은 채로 섞여선 안 된다**. 특히:

* Redis — quota 카운터가 서로 더해지면 B 테넌트의 과금이 A 로 흘러 들어간다.
* MongoDB — scenario 목록 aggregation 에서 다른 테넌트의 시나리오가 노출되면 치명적.
* DLQ — 한 테넌트의 실패 메시지를 다른 테넌트가 재처리 시도하지 않아야 함.

## 결정

모든 외부 키 / 컬렉션에 **테넌트 ID 를 최상위 접두사** 로 강제한다.
접근 레이어에서 prefix 를 자동 조립해 비즈니스 로직이 `tenant_id` 를 까먹을 수 없게 한다.

### 키 규칙

| 저장소 | 형식 | 예시 | 조립 지점 |
|---|---|---|---|
| Redis (quota) | `quota:{tenant}:{scope}:{day}` | `quota:t_acme:tokens:2026-04-18` | `app/core/quota.py::_key()` |
| Redis (session) | `session:{tenant}:{session_id}` | `session:t_acme:s_9f2a…` | `app/core/redis_client.py::SessionStore` |
| Redis (circuit) | `cb:{service}` | `cb:groq_llm` | 서비스 단위 (테넌트 중립) |
| Redis (kill-switch) | `killswitch:global`, `killswitch:tenant:{tenant}` | `killswitch:tenant:t_acme` | `app/domain/kill_switch.py` |
| Redis (DLQ) | `dlq:{tenant}:{kind}` | `dlq:t_acme:tool_invoke` | DLQ publisher |
| Mongo (scenarios) | 컬렉션: `scenarios`, 문서 필드 `tenant_id` (모든 쿼리 `$match` 필수) | — | `ScenarioRepository.*` |
| Mongo (scenario idx) | `(tenant_id, scenario_id, version)` unique index | — | `app/mongo/indexes.py` |

### 규칙

1. **`tenant_id` 는 항상 snake_case + `t_` 접두사**. 형식 검증은 Pydantic 스키마에서 `pattern=r"^t_[a-z0-9_]{1,32}$"`.
2. **테넌트 중립 키** (예: 서비스 단위 CB, `killswitch:global`) 는 명시적으로 `:global` 또는 서비스명 을 쓰고, 테넌트 관련 키와 **prefix 가 절대 겹치지 않도록** 첫 토큰을 다르게 한다.
3. **Redis MULTI / pipeline 실행 시**, 서로 다른 테넌트 키를 한 트랜잭션에 묶지 않는다 — 클러스터 도입 시 cross-slot 오류 + 리뷰 노이즈.
4. **Mongo aggregation 의 첫 stage 는 예외 없이 `$match: { tenant_id }`**. 이를 강제하기 위해 `ScenarioRepository` 가 raw collection 을 노출하지 않는다.
5. **Admin 툴 전용 크로스 테넌트 쿼리** 는 별도 `admin/` 경로로 분리하고 JWT `role=platform_admin` 클레임을 요구한다.

## 대안 비교

* **물리 분리** (테넌트당 DB 인스턴스): 격리 강함, 운영 비용 선형 증가 — 초기 단계에서 과투자.
* **DB 네이티브 테넌시 기능** (Mongo Atlas 다중 DB, Redis ACL + namespace): 일부 경로에서만 적용 가능 — 일관된 규칙 만들기 어려움.
* **암묵적 prefix (코드 관례)**: 사고 발생시 회복 불가. 이미 타사에서 이 방식으로 인시던트가 다수 보고됨 → 불가.

## 결과

* 긍정: 리뷰에서 `tenant_id` 누락은 rg 한 번으로 잡힌다 (`rg "redis\.(incr|set|get)\(.*\)" --glob '!tests'` 로 raw 호출을 탐지).
* 긍정: 테넌트 off-boarding 시 `DEL quota:t_acme:*` + `db.scenarios.deleteMany({tenant_id: "t_acme"})` 단순 2 스텝.
* 주의: 클러스터 샤딩 도입 시, Redis 는 hashtag `{tenant}` 형식으로 슬롯을 묶어 테넌트 단위 pipeline 을 유지할 수 있다. 마이그레이션 시 ADR-### 신설.
* 주의: 크로스 테넌트 통계 대시보드는 in-app aggregation 이 아닌, Prometheus + PromQL 로 구하는 것을 원칙으로 한다 (운영 경계 보호).

## 검증

* 통합 테스트: `tests/integration/test_multitenant_isolation.py` — 테넌트 A 에 저장한 키가 B 에서 `GET` 으로 조회되지 않는지.
* 스태틱 게이트: pre-commit 에서 `app/` 하위에 `redis.set/get(` 문자열을 금지, 래퍼를 통과하도록 강제. (TODO: Track 7 의 추가 작업)
