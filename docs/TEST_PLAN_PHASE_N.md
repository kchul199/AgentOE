# Phase N — 통합 운영포탈 전수 테스트 계획서

> 작성일: 2026-05-15  
> 대상: agentoe monorepo — Phase N ops-portal (N1~N5)  
> 환경: 로컬 개발 (docker/compose.ops-portal.dev.yml)  
> SLO 기준: `docs/reference/slo.md`

---

## 1. 테스트 레이어 개요

```
Phase 0  환경 검증       prerequisites + compose healthcheck
Phase 1  정적 분석       tsc --noEmit + ruff + mypy (portal 범위)
Phase 2  단위 테스트     pytest unit — 외부 의존성 없음
Phase 3  통합 테스트     pytest integration — 실 MongoDB + Redis
Phase 4  기능 테스트     Portal API E2E (auth/RBAC/SSE/config/alerts)
Phase 5  성능 테스트     ASGI direct + SSE concurrent connection 부하
Phase 6  보안 테스트     CSRF / issuer 격리 / brute-force / MFA
Phase 7  배포 테스트     Helm lint + docker build + canary 시뮬레이션
Phase 8  스모크 테스트   전체 스택 상태 최종 확인
```

**Phase 0~3: 필수 게이트** — 실패 시 Phase 4+ 진행 금지.

---

## 2. 실행 명령어 빠른 참조

```bash
# 전체 자동 실행 (권장)
./scripts/run_portal_tests.sh

# Phase 별 개별 실행
./scripts/run_portal_tests.sh --phase 0   # 환경 검증만
./scripts/run_portal_tests.sh --phase 2   # 단위 테스트만
./scripts/run_portal_tests.sh --phase 3   # 통합 테스트만
./scripts/run_portal_tests.sh --phase 5   # 성능 테스트만
./scripts/run_portal_tests.sh --phase 6   # 보안 테스트만
./scripts/run_portal_tests.sh --phase 7   # 배포 테스트만
```

---

## 3. Phase 0 — 환경 검증

### 사전 요구사항

| 항목 | 확인 방법 |
|------|-----------|
| Docker Desktop 실행 중 | `docker info` |
| compose 스택 기동 완료 | `docker compose -f docker/compose.ops-portal.dev.yml ps` |
| backend `/api/v1/health` 200 | `curl -sf http://localhost:8000/api/v1/health` |
| portal_users 계정 존재 | `python scripts/seed_portal_admin.py --no-mfa` |
| Node.js 18+ | `node --version` |
| Python 3.11+ | `python3 --version` |

### 검증 스크립트

```bash
docker compose -f docker/compose.ops-portal.dev.yml up -d
sleep 5

# backend healthcheck
curl -sf http://localhost:8000/api/v1/health | python3 -m json.tool

# MongoDB ping
docker exec agentoe-portal-mongo mongosh \
  "mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin" \
  --quiet --eval "db.adminCommand('ping')"

# Redis ping
docker exec agentoe-portal-redis redis-cli ping
```

**합격 기준:** 모든 명령 exit code 0, healthcheck `{"status":"ok"}` 반환.

---

## 4. Phase 1 — 정적 분석

### 1-1. TypeScript 타입 검사

```bash
cd services/ops-portal
npx tsc --noEmit
```

**합격 기준:** 에러 0개.

### 1-2. Python 린트 (portal 범위)

```bash
cd services/backend
python -m ruff check app/api/v1/routers/auth_portal.py \
  app/api/v1/routers/stream.py \
  app/api/v1/routers/admin.py \
  app/core/auth.py \
  app/domain/audit_emitter.py \
  app/domain/sse_broadcaster.py \
  app/domain/portal_session.py \
  app/infra/kms_client.py \
  app/workers/am_poller.py
```

### 1-3. 타입 체크

```bash
cd services/backend
python -m mypy app/api/v1/routers/auth_portal.py \
  app/core/auth.py \
  app/domain/portal_session.py \
  --ignore-missing-imports --no-strict-optional
```

**합격 기준:** 타입 에러 0개.

---

## 5. Phase 2 — 단위 테스트

### 테스트 파일

| 파일 | 커버 영역 |
|------|-----------|
| `tests/unit/test_portal_rbac.py` | RBAC issuer 격리 (기존) |
| `tests/unit/test_audit_emitter.py` | audit emit graceful degrade (기존) |
| `tests/unit/test_portal_auth_flow.py` | bcrypt SHA-256 pre-hash, password verify, token rotation (**신규**) |

### 실행

```bash
cd services/backend
python -m pytest tests/unit/test_portal_rbac.py \
  tests/unit/test_audit_emitter.py \
  tests/unit/test_portal_auth_flow.py \
  -v --no-header --tb=short \
  --rootdir=tests/unit --no-cov
```

### 합격 기준

| 항목 | 기준 |
|------|------|
| 전체 통과율 | 100% |
| 실행 시간 | < 30초 |
| 경고 | 0개 (deprecation 제외) |

---

## 6. Phase 3 — 통합 테스트

### 사전 조건
- MongoDB, Redis, backend 컨테이너 실행 중
- `MONGODB_URI`, `REDIS_URL` 환경변수 설정

### 테스트 파일

| 파일 | 커버 영역 |
|------|-----------|
| `tests/integration/test_portal_api.py` | Portal auth E2E, SSE 스트림, Config API, Admin proxy (**신규**) |
| `tests/integration/test_auth_e2e.py` | 기존 JWT/JWKS (회귀) |

### 실행

```bash
cd services/backend
export MONGODB_URI="mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin"
export REDIS_URL="redis://localhost:6380/0"
export PORTAL_MFA_ENVELOPE_KEY="0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef"
export PORTAL_JWT_SECRET="dev-portal-jwt-secret-local"
export PORTAL_ORIGIN="http://localhost:5174"

python -m pytest tests/integration/test_portal_api.py -v --tb=short -x
```

### 합격 기준

| TC | 항목 | 기준 |
|----|------|------|
| P-INT-01 | login → MFA 없이(no_mfa=True) access token 발급 | 200 + access cookie |
| P-INT-02 | viewer 토큰으로 portal:operator 엔드포인트 접근 | 403 |
| P-INT-03 | /stream/metrics SSE 연결 + 1회 이벤트 수신 | data: {"ts":...} |
| P-INT-04 | /admin/config/dev GET (viewer 권한) | 200 |
| P-INT-05 | /admin/config/prod PUT (viewer 권한) | 403 |
| P-INT-06 | refresh token rotation — 이전 token 재사용 시 | 401 |
| P-INT-07 | CSRF 헤더 없이 PUT 요청 | 403 |
| P-INT-08 | 잘못된 issuer JWT로 portal route 접근 | 403 |
| P-INT-09 | audit_events 컬렉션에 config 변경 기록 | 1건 확인 |
| P-INT-10 | /auth/portal/logout 후 refresh token 무효화 | 401 |

---

## 7. Phase 4 — 기능 테스트 (UI 수동 + API 자동)

### 4-1. 인증 플로우 (수동)

**사전 조건:** `./start_ops.sh --no-backend` 실행 후 http://localhost:5174 오픈

| 순번 | 액션 | 기대 결과 |
|------|------|-----------|
| F-01 | 잘못된 비밀번호 로그인 | "Invalid credentials" 에러 |
| F-02 | 올바른 ID/PW 로그인 (MFA 비활성) | Dashboard 페이지 이동 |
| F-03 | MFA 활성 계정 로그인 | TOTP 입력 화면 |
| F-04 | 30분 방치 후 API 호출 | 자동 token refresh 또는 로그인 화면 |
| F-05 | viewer 계정으로 Alerts silence 버튼 | 버튼 비활성화 (RBAC gate) |

### 4-2. Dashboard SSE (수동)

| 순번 | 액션 | 기대 결과 |
|------|------|-----------|
| F-06 | Dashboard 접속 | LIVE 배지 + 메트릭 카드 표시 |
| F-07 | backend 10초 중단 후 재시작 | STALE 배지 → 재연결 후 LIVE 복구 |
| F-08 | 네트워크 탭에서 /stream/metrics SSE | `data:` 이벤트 10초마다 수신 |

### 4-3. Config 페이지 (수동)

| 순번 | 액션 | 기대 결과 |
|------|------|-----------|
| F-09 | dev 환경 설정 값 수정 + 저장 | 성공 토스트 + DB 반영 |
| F-10 | diff 탭 클릭 | dev/staging/prod 차이 테이블 |
| F-11 | prod 환경 저장 (viewer 권한) | 저장 버튼 비활성 |
| F-12 | prod 환경 저장 (admin 권한) | 성공 |

### 4-4. 자동 기능 검증 스크립트

```bash
# backend 실행 중일 때 curl 체인으로 핵심 흐름 검증
./scripts/run_portal_tests.sh --phase 4
```

---

## 8. Phase 5 — 성능 테스트

### 5-1. API 응답 지연 (ASGI direct)

**목표 SLO:** P95 ≤ 500ms, 성공률 ≥ 99.9%

```bash
cd services/backend
python -m pytest tests/performance/test_portal_load.py -v --tb=short
```

### 5-2. SSE 동시 연결 (실제 서버)

**목표:** 50 동시 SSE 연결 × 30초 유지, 에러 0

```bash
# backend 실행 중일 때
python services/backend/tests/performance/test_portal_load.py --sse-concurrent 50
```

### 5-3. 성능 기준표

| 항목 | 목표 | 측정 방법 |
|------|------|-----------|
| login 응답 (bcrypt) | P95 ≤ 1,000ms | ASGI direct × 20 |
| /stream/metrics 첫 이벤트 | ≤ 2,000ms | httpx + SSE |
| /admin/config GET | P95 ≤ 200ms | ASGI direct × 100 |
| /admin/config PUT | P95 ≤ 300ms | ASGI direct × 50 |
| SSE 50 동시 연결 유지 | 30초 무단절 | asyncio gather |
| refresh token rotation | P95 ≤ 500ms | ASGI direct × 50 |

---

## 9. Phase 6 — 보안 테스트

### 6-1. CSRF double-submit

```bash
# CSRF 헤더 없이 PUT → 403
curl -sf -X PUT http://localhost:8000/api/v1/admin/config/dev \
  -H "Content-Type: application/json" \
  -d '{"updated_by":"test","values":{}}' \
  -w "\nHTTP: %{http_code}" | grep "HTTP: 403"

# 올바른 CSRF (cookie + header 동일값) → 통과
```

### 6-2. JWT issuer 격리

```bash
cd services/backend
python -m pytest tests/unit/test_portal_rbac.py -v -k "issuer"
```

**합격 기준:** agentoe-api issuer 토큰으로 portal route 접근 → 전량 403.

### 6-3. brute-force 보호

```bash
# 5회 실패 → 6번째 요청에서 429 반환 기대
for i in {1..6}; do
  CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    -X POST http://localhost:8000/api/v1/auth/portal/login \
    -H "Content-Type: application/json" \
    -d '{"username":"admin","password":"wrong"}')
  echo "시도 $i: HTTP $CODE"
done
```

### 6-4. MFA envelope 암호화

```bash
cd services/backend
python -m pytest tests/unit/test_portal_auth_flow.py -v -k "mfa_encrypt"
```

### 6-5. Bandit 보안 정적 분석

```bash
cd services/backend
pip install bandit --break-system-packages -q
bandit -r app/api/v1/routers/auth_portal.py \
  app/domain/portal_session.py \
  app/infra/kms_client.py \
  -ll -q
```

**합격 기준:** HIGH severity 0건.

---

## 10. Phase 7 — 배포 테스트

### 7-1. Helm lint (staging + prod)

```bash
helm lint deploy/helm/agentoe-portal \
  -f deploy/helm/values/staging/portal.values.yaml

helm lint deploy/helm/agentoe-portal \
  -f deploy/helm/values/prod/portal.values.yaml
```

### 7-2. Docker 빌드 검증

```bash
docker build -t ops-portal:test services/ops-portal/ \
  --build-arg VCS_REF=test --build-arg BUILD_DATE=$(date -u +%Y-%m-%dT%H:%M:%SZ)

# 이미지 smoke test
docker run --rm -p 4000:80 -d ops-portal:test
sleep 3
curl -sf http://localhost:4000/healthz || echo "healthz 없음 (nginx 기본 페이지 OK)"
docker stop $(docker ps -q --filter ancestor=ops-portal:test)
```

### 7-3. Canary 배포 시뮬레이션

```bash
# dry-run 으로 5단계 게이트 흐름 검증
./scripts/portal_prod_deploy.sh --image-tag test --dry-run
```

**합격 기준:** gate 1~5 전부 `[DRY-RUN]` 출력 + exit 0.

### 7-4. Helm template 렌더링 + kubeconform

```bash
helm template agentoe-portal-staging deploy/helm/agentoe-portal \
  -f deploy/helm/values/staging/portal.values.yaml \
  > /tmp/portal-staging.yaml

# kubeconform 설치된 경우
kubeconform -strict -ignore-missing-schemas /tmp/portal-staging.yaml
echo "렌더 라인수: $(wc -l < /tmp/portal-staging.yaml)"
```

---

## 11. Phase 8 — 최종 스모크 테스트

전체 스택 기동 후 핵심 엔드포인트 체인 검증:

```bash
./scripts/run_portal_tests.sh --phase 8
```

내부 순서:
1. `GET /api/v1/health` → `{"status":"ok"}`
2. `POST /api/v1/auth/portal/login` → access token 쿠키
3. `GET /api/v1/admin/env/info` (viewer) → `200`
4. `GET /api/v1/stream/metrics` SSE 1이벤트 수신 → `data:` 포함
5. `GET /api/v1/admin/config/dev` → `200`
6. `POST /api/v1/auth/portal/logout` → `200` + 쿠키 삭제

**합격 기준:** 6단계 전부 pass.

---

## 12. 합격/실패 기준 요약

| Phase | 합격 기준 | 실패 시 |
|-------|-----------|---------|
| 0 환경 | 전체 healthcheck green | compose 재시작 |
| 1 정적 | 에러 0 | 즉시 수정 후 재실행 |
| 2 단위 | 100% pass | 실패 케이스 수정 |
| 3 통합 | 10/10 TC pass | 버그 수정 후 재실행 |
| 4 기능 | 12/12 수동 체크 | UI 버그 수정 |
| 5 성능 | P95 기준 전부 충족 | 병목 분석 후 최적화 |
| 6 보안 | CSRF/issuer/brute-force 전부 차단 | 즉시 수정 (머지 금지) |
| 7 배포 | helm lint + docker build + dry-run pass | chart/Dockerfile 수정 |
| 8 스모크 | 6단계 전부 pass | staging 배포 금지 |

**Phase 6 실패 = 절대 머지 금지.**

---

## 13. 관련 파일

| 파일 | 역할 |
|------|------|
| `scripts/run_portal_tests.sh` | 전체 테스트 자동화 실행기 |
| `tests/unit/test_portal_auth_flow.py` | Phase 2 단위 테스트 |
| `tests/integration/test_portal_api.py` | Phase 3 통합 테스트 |
| `tests/performance/test_portal_load.py` | Phase 5 성능 테스트 |
| `docs/reference/slo.md` | SLO 임계값 (단일 진실 소스) |
| `docker/compose.ops-portal.dev.yml` | 로컬 인프라 스택 |
