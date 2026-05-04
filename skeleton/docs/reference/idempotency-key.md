# Idempotency-Key — API 사용 가이드

## 목적

네트워크 재시도, 클라이언트 재실행, 중복 클릭으로 인한 **부수효과 중복**(시나리오 publish, 테넌트 생성 등)을 막기 위해 표준 `Idempotency-Key` 헤더를 지원한다.

`Stripe / GitHub / AWS` 가 사용하는 패턴과 동일한 의미론을 따른다 — **같은 key + 같은 body 로 재요청 시, 서버는 원본 응답을 그대로 재생한다.**

## 적용 대상

- **메서드**: `POST`, `PUT`, `PATCH`, `DELETE`
- **활성 조건**: 요청에 `Idempotency-Key` 헤더가 포함된 경우 (opt-in)
- **강제 적용 경로** (헤더 없으면 400): `IDEMPOTENCY_REQUIRED_PATHS` 환경 변수의 prefix CSV
- **제외**: WebSocket 업그레이드(`/api/v1/ws/*`), 헬스체크(`/api/v1/health/*`), 메트릭(`/api/v1/metrics`), OpenAPI 문서

## 헤더 규격

| 항목 | 값 |
|------|----|
| 이름 | `Idempotency-Key` |
| 형식 | 영숫자 + `-` + `_` |
| 길이 | 8 ~ 128 자 |
| 권장 | UUID v4 (예: `8e1c0f72-9b3a-4d6e-9a18-2a46e1f0c0d8`) |

위반 시 400 응답: `{"error": "IDEMPOTENCY_KEY_INVALID", "message": ...}`

## 응답 매트릭스

| 시나리오 | 상태코드 | 비고 |
|---------|---------|------|
| 첫 요청 (정상) | 핸들러 결과 그대로 | 응답을 Redis 에 캐시 |
| 같은 key + 같은 body | 핸들러 결과 그대로 | `Idempotent-Replay: true` 헤더 부착 |
| 같은 key + 다른 body | 422 | `IDEMPOTENCY_KEY_MISMATCH` |
| 같은 key, 진행 중 | 409 + `Retry-After: 5` | `IDEMPOTENCY_IN_PROGRESS` |
| 핸들러 5xx | 핸들러 결과 그대로 | Redis 슬롯 즉시 해제 → 재시도 가능 |
| Redis 장애 | 핸들러 결과 그대로 | fail-open, 메트릭 `idempotency_acquire_failed` 카운트 |

## TTL / 캐시 정책

- 기본 TTL **600초** (`IDEMPOTENCY_TTL_SECONDS`).
- 응답 바디가 `IDEMPOTENCY_MAX_BODY_BYTES` (기본 256KB) 초과 시 메타만 저장하고 바디는 비움 — replay 시 빈 바디 + `Idempotent-Replay: truncated`.
- 응답 헤더 중 `Date`, `X-Request-Id`, `X-Trace-Id`, `Content-Length`, `Server` 등 매 요청마다 새로 붙어야 정상인 헤더는 replay 시 자동 제거.

## 클라이언트 권장 패턴

```bash
# 동일한 의도의 요청은 같은 key 로 재시도
KEY=$(uuidgen)
for i in 1 2 3; do
  curl -X POST https://api.agentoe.io/api/v1/scenarios/sc-1/publish \
    -H "Authorization: Bearer $TOKEN" \
    -H "Idempotency-Key: $KEY" \
    -H "Content-Type: application/json" \
    -d '{"version": 3}' && break
  sleep 2
done
```

리트라이 라이브러리를 쓸 때는 **첫 시도와 모든 재시도가 같은 key 를 공유**해야 한다. 매 시도마다 새 key 를 생성하면 idempotency 가 무효가 된다.

## 운영 메트릭

미들웨어가 `structlog` 으로 남기는 키 이벤트:

| event | 의미 |
|-------|------|
| `idempotency_replay` | 캐시된 응답 재생 |
| `idempotency_in_flight` | 처리 중인 동일 key 요청 (409) |
| `idempotency_key_reused_with_different_body` | 422 회신 (잘못된 클라이언트 사용) |
| `idempotency_acquire_failed` | Redis 장애 — fail-open 으로 진행 |
| `idempotency_store_failed` | 응답 캐시 저장 실패 (다음 재시도는 idempotency 없이 처리됨) |

## 강제 적용 경로 설정 (운영 권장)

`IDEMPOTENCY_REQUIRED_PATHS=/api/v1/scenarios/,/api/v1/admin/tenants` 처럼 CSV 로 prefix 를 지정하면, 해당 경로의 mutating 요청은 헤더 없이 호출 시 즉시 400 으로 거부된다. 결제, 외부 시스템 호출처럼 **부수효과 중복이 치명적인 경로**에 권장.

## 관련 ADR

- `docs/adr/0007-idempotency-key.md` — 도입 배경, 대안(서버 측 dedup vs Outbox), TTL 결정 근거.
