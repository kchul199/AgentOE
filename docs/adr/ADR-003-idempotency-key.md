# ADR-003: Idempotency-Key 미들웨어 도입

| 항목 | 값 |
|---|---|
| 상태 | Accepted |
| 작성일 | 2026-04-20 |
| 관련 코드 | `app/middleware/idempotency_middleware.py`, `app/core/idempotency.py` |
| 관련 문서 | `docs/reference/idempotency-key.md` |

## 맥락

AgentOE 의 mutating REST 엔드포인트는 외부 상태에 **부수효과**를 만든다:

- `POST /api/v1/scenarios/{id}/publish` — 시나리오 publish + 이전 published 버전을 unpublish (이중 커밋)
- `POST /api/v1/admin/tenants` — 테넌트 신규 생성 + Mongo 도큐먼트 + Redis namespace 초기화
- `PATCH /api/v1/admin/tenants/{id}` — 쿼터/예산 갱신 + 이벤트 로그

이 중 publish 는 scenario DSL 의 실제 플레이 트래픽을 스위칭한다. 클라이언트가 네트워크 타임아웃 후 재시도하면 **같은 버전이 두 번 publish → unpublish 이벤트가 꼬이거나 감사로그가 중복 기록**되는 사례가 스테이징에서 관측됐다 (2026-03 내부 티켓 OE-412).

## 대안

1. **엔드포인트별 자체 dedup (Outbox 패턴)** — 핸들러가 Mongo 유니크 키로 같은 요청을 인지하고 원본 응답을 재생.
   - 장점: 비즈니스 의미가 명확.
   - 단점: 엔드포인트마다 따로 구현 → 누락/불일치 위험. 테스트 비용 N 배.
2. **클라이언트 측 dedup (Retry-After + 클라이언트 ID)** — 서버는 그대로 두고, SDK 만 동일 요청을 버퍼링.
   - 단점: 웹/모바일/서드파티 통합 모두에 동일한 로직 강제 불가. 외부 연동 파트너가 많아질수록 실효성 ↓.
3. **Idempotency-Key 미들웨어 (채택)** — Stripe/GitHub/AWS 가 쓰는 표준 헤더.
   - 장점: 헤더 하나로 opt-in. 엔드포인트 코드 변경 없음. 클라이언트는 업계 표준 retry 라이브러리 재사용 가능.
   - 단점: Redis 의존. 바디 해시 비교로 같은 key 다른 body 를 422 로 막아야 함.

## 결정

미들웨어 하나 (`IdempotencyMiddleware`) 를 전역에 붙이고, **헤더가 있을 때만** 동작 (opt-in) 한다.
운영상 치명적인 경로에는 `IDEMPOTENCY_REQUIRED_PATHS` 환경 변수로 헤더 강제를 걸 수 있게 했다.

### 저장소

- Redis `SET ... NX EX 600` 으로 슬롯 선점.
- 완료 응답을 같은 key 에 덮어써 원본 재생 가능하게 한다.
- 기본 TTL 600 초 — 네트워크 재시도 최대치(클라이언트 exponential backoff) + 장시간 재시도 마진.

### Fail-open

Redis 장애 시 미들웨어는 `acquired=True` 로 간주하고 요청을 흘려보낸다.
CLAUDE.md 의 대원칙 *“통화가 끊기지 않음이 최우선”* 에 부합: idempotency 는 **편의 기능**이며, Redis 가 잠깐 빠졌다고 mutating 요청 전체를 500 으로 막는 건 과잉이다.
대신 구조화 로그에 `idempotency_acquire_failed` 를 남기고, 운영팀이 Grafana 알림으로 가시화한다.

### 5xx 시 슬롯 해제

핸들러가 5xx 를 내면 `release_slot` 으로 key 를 지운다.
→ 서버 장애로 실패한 요청에 대한 **정상 재시도 경로가 막히지 않도록** 보장.
4xx 는 유효한 클라이언트 결과이므로 캐시된다 (재시도해도 같은 4xx 가 나와야 함).

### Body 미스매치 검증

같은 key 로 **다른 바디**를 보내면 422 반환. 이는:
- 클라이언트 버그(예: key 를 세션 전역에 고정하고 바디만 바꿔보는 케이스)
- 리플레이/재사용 공격
두 가지 모두 방어한다.

### 바디 크기 한계

응답 바디가 `IDEMPOTENCY_MAX_BODY_BYTES` (기본 256KB) 를 넘으면 메타만 기록하고 바디는 비워 저장.
대용량 파일 리스팅 같은 특수 케이스에서 Redis 메모리를 폭파시키지 않기 위한 가드.
Replay 시 `Idempotent-Replay: truncated` 가 붙어 클라이언트가 인지할 수 있다.

## 결과

- 새 엔드포인트 추가 시 별도 작업 없이 `Idempotency-Key` 헤더 지원.
- 결제/배포성 API 는 `IDEMPOTENCY_REQUIRED_PATHS` 로 헤더 강제.
- Grafana 알림 1 개 추가: `rate(idempotency_acquire_failed[5m]) > 0.5/s` → Redis 이상 징후 조기 인지.
- `docs/reference/idempotency-key.md` 를 공개 API 문서에 링크해 외부 파트너 SDK 가 같은 규약을 구현하게 한다.

## 리스크 / 후속 조치

- **Redis 복구 직후 스로틀** — 대규모 재시도 폭주 상황에서 Redis 가 회복되자마자 슬롯 선점 경쟁이 피크를 찍을 수 있음. 현재는 `RateLimitMiddleware` 가 이를 흡수한다. 관측 후 필요 시 idempotency 앞단에 별도 Admission 이 필요할지 재평가.
- **Prometheus 메트릭 미노출** — 현재는 구조화 로그만 남긴다. 후속 PR 에서 `app/core/metrics.py` 에 `record_idempotency_event(kind)` 를 추가해 Grafana 패널로 연결 예정.
