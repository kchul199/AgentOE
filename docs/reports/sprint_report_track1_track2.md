# AgentOE 스프린트 종료 리포트 — Track 1 + Track 2 (P1)

작성일: 2026-04-19

## 요약

지난 스프린트에서 이미 반영된 **P0 운영 패치 9건** 위에,
이번 스프린트에서 **Track 1 (에이전틱 단위테스트 + CI 게이트)** 과
**Track 2 P1 Top 5 (보안·과금·로그 품질)** 을 skeleton 에 직접 반영.

모든 수정 파일은 `python -m py_compile` 통과, 핵심 로직은
Redis/structlog mock 환경에서 **단위 수준으로 동작 검증 완료**.

## Track 1 — 에이전틱 단위테스트 & CI

| 항목 | 파일 | 상태 |
|---|---|---|
| 1-A pytest·ruff·mypy 러너 | `backend/pyproject.toml`, `backend/app/core/config.py` | 완료 |
| 1-B DSL/compiler/router/nodes 테스트 | `backend/tests/unit/agentic/{conftest,test_scenario_dsl,test_scenario_compiler,test_router,test_nodes}.py` | 완료 |
| 1-C GitHub Actions CI | `.github/workflows/ci.yml` (`agentic-unit-test` job, `--cov=app/agentic --cov-fail-under=70`) | 완료 |

## Track 2 — 운영 보안 P1 Top 5

| 항목 | 파일 | 핵심 구현 |
|---|---|---|
| 2-a WebSocket Origin 검증 | `app/api/v1/routers/vbgw.py` | `_is_origin_allowed()` + close code `4005` (accept 이전) |
| 2-b JWKS 캐시 + kid 회전 | `app/core/jwks_cache.py`, `app/core/auth.py` | `_decode_with_jwks/legacy_hs` 분기, kid miss 시 1회 force-refresh, 30s 실패 백오프, stale 가용성 우선 |
| 2-c X-Tenant-Id 위변조 차단 | `app/core/auth.py` | `ENFORCE_TENANT_HEADER_MATCH` 로 JWT claim ↔ 헤더 교차검증 |
| 2-d LLM 토큰/비용 일일 쿼터 | `app/core/quota.py`, `app/services/llm_service.py`, `app/agentic/nodes/llm_node.py` | Redis INCRBY 일 단위 롤업, `fallback/reject/warn` 정책, `QuotaExceededError(graceful)` → 시나리오 fallback 또는 429 |
| 2-e 로그 context 누수 방지 | `app/core/logging.py`, `app/middleware/logging_middleware.py`, `app/services/ai_pipeline.py`, `app/services/call_session_orchestrator.py`, `app/api/v1/routers/vbgw.py` | `scoped_context()`, `unbind_request_context()`, 파이프라인 stage/policy_level 해제, WS 세션 바인딩 + finally 클리어, HTTP access log 를 bound context 안에서 기록 |

## 신규 단위 테스트

- `tests/unit/test_logging_context.py` — `scoped_context` bind/unbind 대칭성, 중첩 스코프, 예외 경로, None 스킵 (5 케이스).
- `tests/unit/test_quota.py` — 빈 상태, tokens/cost 초과, fallback/reject/warn 정책, disabled short-circuit, commit pipeline, zero no-op, RedisError fail-open (9 케이스).

로컬 smoke 러너로 **Track 2-e 4/4 + Track 2-d 9/9 케이스 모두 통과**.

## 검증 결과

- 수정 파일 12개 전체 `py_compile` 통과.
- 전체 `backend/app/**/*.py` syntax sweep 에러 0건.
- 쿼터/로그 context 모듈은 stub Redis + real structlog 환경에서 런타임 검증 완료.
- CI 워크플로는 Unit / Agentic Unit(cov 70) / Integration / Docker Build / Security 5 잡으로 구성.

## 잔여 / 다음 스프린트 후보

1. **Track 2 P2** — 세션 복구 idempotency, WS back-pressure, 감사 로그 WORM 저장.
2. **Track 3** — Prometheus 메트릭 보강 (quota_* 카운터, jwks_refresh_* 히스토그램).
3. **Track 4** — GUI 시나리오 빌더 (React Flow) 실제 구현 (HTML 목업 → 번들링).
4. **통합 테스트** — JWKS 엔드포인트 mock 서버 + Redis fakeredis 로 end-to-end 보안 시나리오.
5. **운영 Runbook** — `LLM_QUOTA_EXCEEDED_BEHAVIOR` 전환 절차, JWKS kid 회전 대응 절차.
