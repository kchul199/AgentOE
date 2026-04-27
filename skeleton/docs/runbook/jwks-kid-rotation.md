# Runbook: JWKS kid 회전

| 항목 | 값 |
|---|---|
| 작성일 | 2026-02-14 |
| 최종 점검일 | 2026-04-18 |
| 대상 온콜 | platform-oncall + IAM admin |
| 관련 코드 | `app/core/jwks_cache.py`, `app/core/auth.py` |

## 배경

AgentOE 는 외부 IdP (예: Auth0 / Keycloak) 가 발급한 JWT 를 검증한다.
공개키는 `/.well-known/jwks.json` 에서 가져오고, `kid` 헤더로 키를 선택한다.
캐시는 메모리 + TTL (기본 10분) + `kid miss → force refresh` 전략.

회전은 두 가지 경로:

1. **계획된 회전** — IdP 에서 새 key pair 생성, 기존 kid 와 잠시 공존시키다가 폐기.
2. **비상 회전** — 키 유출 의심 시 즉시 폐기.

## 탐지 / 트리거

* Metric: `agentoe_jwks_lookups_total{result="miss"}` rate 가 baseline 대비 5x 이상
* Metric: `agentoe_jwks_refresh_duration_seconds{result="fail"}` 이 1회 이상 발생
* 로그: `jwks: unknown kid=<...>` 대량 발생

## 계획된 회전 절차 (무중단)

### 1. IdP 에 새 키 추가

* 새 RSA (또는 EC) 키를 생성, `/.well-known/jwks.json` 응답에 **두 kid 가 함께** 반환되도록 설정.
* 이 시점엔 아직 기존 kid 로 서명 — AgentOE 동작 영향 없음.

### 2. AgentOE 캐시 워밍

JWKS 캐시는 요청 들어올 때 lazy 로드 — 강제 warm-up 은 선택 사항.
```bash
kubectl -n agentoe exec deploy/backend-api -- \
  curl -s -X POST http://localhost:8000/internal/jwks/refresh  # 가상 admin endpoint
```
_admin endpoint 가 아직 구현되지 않은 경우는 5~10분 내 자연 캐시 갱신을 기다린다._

### 3. IdP 에서 서명 키를 새 kid 로 전환

* 이 순간부터 **신규 JWT 는 새 kid**, 기존 발급된 JWT 는 **이전 kid** 로 검증됨.
* AgentOE 는 두 kid 모두 캐시에 보유, 정상 동작.

### 4. 만료 대기

* 발급된 JWT 의 최대 TTL 이 지날 때까지 (예: 24h) 양쪽 kid 를 유지.

### 5. IdP 에서 이전 kid 폐기

* `/.well-known/jwks.json` 에서 이전 kid 제거.
* AgentOE 의 캐시가 다음 TTL 에서 갱신되며 이전 kid 에 대한 요청은 `kid not found → 401`.

### 6. 모니터링

* 회전 후 24~48h 동안 `agentoe_jwks_lookups_total{result="miss"}` 추이 관찰.
* 만약 **구형 클라이언트** 가 만료된 이전 토큰을 끝없이 재시도 중이라면, 해당 테넌트 관리자와 연락.

## 비상 회전 절차 (키 유출 의심)

### 1. IdP 에서 즉시 키 revoke

### 2. AgentOE 캐시 강제 무효화

캐시 TTL(기본 10분)을 기다리면 10분 틈이 생긴다. 즉각 반영 필요:
```bash
kubectl -n agentoe rollout restart deploy/backend-api
```
_restart 는 process memory 를 초기화 → 다음 요청 시 새 JWKS fetch._

### 3. 활성 세션 강제 종료 (필요 시)

```bash
# Redis 의 session 키 전수 삭제 — 매우 공격적. 고객 동의 하에만.
kubectl -n data exec redis-0 -- redis-cli --scan --pattern 'session:*' | \
  xargs -n 100 redis-cli DEL
```

### 4. 사후 커뮤니케이션

* 고객에게 토큰 재발급을 공지.
* 유출 범위 분석 후 24h 내 보고서.

## 테스트

회전 후 검증:
```bash
# 유효 토큰
curl -H "Authorization: Bearer <new_token>" https://api.agentoe.../api/v1/scenarios
# → 200

# 폐기된 이전 토큰
curl -H "Authorization: Bearer <old_token>" https://api.agentoe.../api/v1/scenarios
# → 401 { "code": "AUTH_INVALID", ... }
```

## 자주 나오는 실수

* **IdP 회전 직후 AgentOE 의 캐시 TTL 이 너무 길어 로그인 실패 증가** — 계획된 회전 1 단계와 3 단계 사이에 최소 TTL × 2 (기본 20분) 간격을 둘 것.
* **`/.well-known/jwks.json` 응답이 CDN/캐시 서버에서 캐싱되고 있어 AgentOE 가 오래된 응답을 받는다** — IdP 측에서 Cache-Control 을 `no-store` 또는 짧게 설정.
* **캐시 공유**: 현재 JWKS 캐시는 프로세스 로컬. 파드 수가 많으면 각각 fetch → IdP 측 rate limit 주의.
  대안: Redis 캐시로 이전 (ADR 신설 필요).

## 관련

* [ADR-002: 멀티테넌트 키 네임스페이스](../adr/ADR-002-tenant-key-namespace.md)
* 메트릭: `agentoe_jwks_lookups_total`, `agentoe_jwks_refresh_duration_seconds` → [Prometheus 메트릭 카탈로그](../reference/prometheus-metrics.md)
