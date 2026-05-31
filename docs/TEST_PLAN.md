# agentoe — 로컬 전수 테스트 계획서

> 작성일: 2026-05-05  
> 대상: agentoe monorepo (Phase M 완료 시점)  
> 목적: 로컬 개발환경에서 staging cutover 전 전체 품질 게이트 검증

---

## 개요 및 테스트 레이어

```
Phase 0  환경 셋업 (prerequisites + docker stack up)
Phase 1  정적 분석 & 린트 (ruff, mypy, go vet, gosec)
Phase 2  단위 테스트 (pytest unit, Go unit — 외부 의존성 없음)
Phase 3  통합 테스트 (pytest integration + gRPC smoke)
Phase 4  E2E 기능 테스트 (full stack — HTTP API + WebSocket + gRPC)
Phase 5  성능 테스트 (k6 load test — SLO 기준 검증)
Phase 6  배포 테스트 (Helm lint, docker build, canary 시뮬레이션)
Phase 7  보안 테스트 (bandit, gosec, JWT/JWKS, 인증 경계)
```

각 Phase 는 독립 실행 가능하지만 권장 순서는 위 순서를 따른다.  
**Phase 0~3 은 필수 게이트** — 실패 시 다음 Phase 진행 금지.

---

## Phase 0 — 환경 셋업

### 0-1. 사전 요구사항 확인

| 도구 | 최소 버전 | 확인 명령 |
|------|-----------|-----------|
| Docker Engine | 24.x | `docker --version` |
| Docker Compose v2 | 2.24.x | `docker compose version` |
| Python | 3.11 | `python3 --version` |
| Go | 1.22 | `go version` |
| grpcurl | any | `grpcurl --version` |
| k6 | 0.50+ | `k6 version` |
| promtool | any | `promtool --version` |

### 0-2. .env 설정

```bash
# 1) .env 생성 (최초 1회)
cp services/backend/.env.example services/backend/.env

# 2) 최소 필수값 설정 (실제 키 불필요 — dev stub 사용)
#    .env 에서 반드시 설정:
#    GROQ_API_KEY=sk-test-local
#    JWT_SECRET=local-dev-secret-32chars-minimum
#    MONGODB_URI=mongodb://admin:devpass@localhost:27017/?replicaSet=rs0
#    REDIS_URL=redis://localhost:6379/0
#    GRPC_ENABLED=true
#    GRPC_PORT=50051
#    GRPC_REFLECTION_ENABLED=true
```

### 0-3. 로컬 스택 기동

```bash
# 방법 A: 자동 셋업 스크립트 (추천)
bash scripts/setup-local.sh

# 방법 B: 수동 (단계별 확인)
docker compose -f docker/compose.backend.yml pull
docker compose -f docker/compose.backend.yml up -d mongo-primary redis
sleep 10
docker compose -f docker/compose.backend.yml up mongo-init
docker compose -f docker/compose.backend.yml up -d api nginx
```

### 0-4. 스택 헬스 확인

```bash
# HTTP liveness
curl -sf http://localhost:8000/api/v1/livez | python3 -m json.tool

# gRPC port open 확인
nc -z localhost 50051 && echo "gRPC OK"

# gRPC reflection 확인 (grpcurl)
grpcurl -plaintext localhost:50051 list

# MongoDB Replica Set 상태
docker compose -f docker/compose.backend.yml exec -T mongo-primary \
  mongosh --quiet --eval "rs.status().members.map(m => ({name:m.name, state:m.stateStr}))"

# Redis ping
docker compose -f docker/compose.backend.yml exec -T redis redis-cli ping
```

### 0-5. 합격 기준

- [ ] API `GET /api/v1/livez` → `{"status":"ok"}`
- [ ] gRPC port 50051 TCP open
- [ ] MongoDB RS primary 1개 + secondary 1개 확인
- [ ] Redis PONG 응답

---

## Phase 1 — 정적 분석 & 린트

### 1-1. Python (backend)

```bash
cd services/backend

# 린트
ruff check app/ tests/
# 예상: 0 errors

# 포맷 확인 (수정 X, 확인만)
ruff format --check app/ tests/

# 타입 체크
mypy app/ --ignore-missing-imports
# 예상: Success: no issues found
```

### 1-2. Go (vbgw-bridge, vbgw-orchestrator, vbgw-ai)

```bash
# 빌드 가능 여부 확인 (전 서비스)
for svc in services/vbgw-bridge services/vbgw-orchestrator services/vbgw-ai; do
  echo "=== $svc ==="
  (cd "$svc" && go build ./... && echo "BUILD OK") || echo "BUILD FAILED"
done

# vet (코드 정확성)
for svc in services/vbgw-bridge services/vbgw-orchestrator services/vbgw-ai; do
  (cd "$svc" && go vet ./...)
done
```

### 1-3. Proto drift 검증

```bash
# contracts/gen 이 proto 와 동기화 확인
cd contracts && make gen
git diff --exit-code gen/
# 예상: 변경사항 없음 (exit 0)
```

### 1-4. Helm 차트 린트

```bash
cd deploy/helm

# 각 차트 × 환경 조합 린트
for chart in agentoe-backend agentoe-frontend vbgw; do
  for env in staging prod; do
    echo "=== helm lint: $chart / $env ==="
    helm lint "$chart" \
      -f "../../../deploy/helm/$chart/values/${env}.values.yaml" \
      --strict
  done
done
```

### 1-5. Prometheus Rules 검증

```bash
promtool check rules \
  deploy/k8s-bootstrap/manifests/prometheus-rules/*.yaml
# 예상: SUCCESS
```

### 1-6. GHA YAML 파싱 확인

```bash
python3 -c '
import yaml, glob
files = glob.glob(".github/**/*.yml", recursive=True)
for f in files:
    yaml.safe_load(open(f))
    print(f"OK: {f}")
'
```

### 1-7. 합격 기준

- [ ] ruff: 0 errors
- [ ] mypy: no issues
- [ ] Go build: 전 서비스 성공
- [ ] Proto drift: git diff clean
- [ ] Helm lint: strict 모드 통과
- [ ] Prometheus rules: SUCCESS

---

## Phase 2 — 단위 테스트

> 외부 의존성(DB, Redis, 외부 API) 없이 실행. 빠른 피드백 루프.

### 2-1. Python 단위 테스트

```bash
cd services/backend
pip install -e ".[dev]" --break-system-packages

# 전체 단위 테스트
pytest tests/unit/ -v --tb=short \
  --cov=app --cov-report=term-missing --cov-report=xml:coverage-unit.xml

# 커버리지 목표: app/ 전체 70% 이상
# 반드시 통과해야 할 핵심 모듈:
pytest tests/unit/test_circuit_breaker.py -v        # CB 상태 머신
pytest tests/unit/test_kill_switch.py -v            # kill_switch 플래그
pytest tests/unit/test_session_fsm.py -v            # 통화 세션 FSM
pytest tests/unit/test_session_recovery.py -v       # 세션 복구 로직
pytest tests/unit/test_metrics.py -v                # Prometheus 카운터
pytest tests/unit/test_ws_backpressure.py -v        # WS backpressure
pytest tests/unit/agentic/ -v                       # DSL + 컴파일러 + 라우터
pytest tests/unit/grpc_server/ -v                   # gRPC 서비스 매핑
```

### 2-2. Agentic 모듈 집중 테스트

```bash
cd services/backend

# DSL 파서 — 시나리오 JSON 스키마 위반 시 컴파일 에러 발생 확인
pytest tests/unit/agentic/test_scenario_dsl.py -v

# 컴파일러 — 노드 타입별 코드 생성 정확성
pytest tests/unit/agentic/test_scenario_compiler.py -v

# 라우터 — fallback + 조건 분기 정확성
pytest tests/unit/agentic/test_router.py -v

# 노드 실행기 — 각 노드 타입 독립 실행
pytest tests/unit/agentic/test_nodes.py -v
```

### 2-3. Go 단위 테스트

```bash
# vbgw-orchestrator (ESL, IVR, CDR, API)
cd services/vbgw-orchestrator
go test ./... -v -count=1 -race \
  -coverprofile=coverage-orchestrator.out
go tool cover -func=coverage-orchestrator.out | tail -3

# vbgw-bridge (VAD, barge-in, gRPC, WS)
cd services/vbgw-bridge
go test ./... -v -count=1 -race \
  -coverprofile=coverage-bridge.out
go tool cover -func=coverage-bridge.out | tail -3
```

### 2-4. 합격 기준

- [ ] pytest unit: 0 failed
- [ ] Python 커버리지: app/ ≥ 70%
- [ ] Go test (orchestrator): PASS, race-free
- [ ] Go test (bridge): PASS, race-free
- [ ] FSM/CB/kill_switch 핵심 3개 모듈: 100% 통과

---

## Phase 3 — 통합 테스트

> Docker stack 이 올라와 있는 상태 (Phase 0 완료) 에서 실행.

### 3-1. backend 통합 테스트

```bash
cd services/backend

# 통합 테스트 환경변수 설정
export MONGODB_URI="mongodb://admin:devpass@localhost:27017/?replicaSet=rs0"
export REDIS_URL="redis://localhost:6379/0"
export JWT_SECRET="local-dev-secret-32chars-minimum"

# 통합 테스트 실행 (DB/Redis 실제 연결)
pytest tests/integration/ -v --tb=short -m integration \
  --cov=app --cov-report=xml:coverage-integration.xml

# 각 통합 테스트 의미:
# test_auth_e2e.py       — JWKS → JWT 발급 → 인증 E2E
# test_scenarios_api.py  — 시나리오 CRUD REST API
# test_metrics_api.py    — /metrics 엔드포인트 Prometheus 포맷 확인
# test_vbgw_integration.py — backend ↔ vbgw 연결 사전 검증
```

### 3-2. gRPC smoke (backend 직접)

```bash
# grpcurl 로 직접 검증
grpcurl -plaintext localhost:50051 list
grpcurl -plaintext localhost:50051 \
  grpc.health.v1.Health/Check

# smoke client (3회 호출)
python3 scripts/integration/smoke_grpc_client.py \
  --addr localhost:50051 --calls 3 --tenant smoke
```

### 3-3. bridge → backend 통합 (SKIP_VBGW=0)

```bash
# 전체 통합 환경 기동 (backend + vbgw 모두)
AGENTOE_DIR=~/AgenticOE_v2 bash scripts/integration/dev-integration.sh up

# 결과 확인
bash scripts/integration/dev-integration.sh status

# bridge → backend gRPC 도달 확인
docker exec vbgw-bridge nc -z agentoe-api 50051 && echo "BRIDGE→BACKEND OK"

# 로그 확인
bash scripts/integration/dev-integration.sh logs
```

### 3-4. REST API 기능 검증 (HTTPie / curl)

```bash
BASE="http://localhost:8000"

# 1) Health
curl -sf $BASE/api/v1/livez | python3 -m json.tool
curl -sf $BASE/api/v1/readyz | python3 -m json.tool

# 2) 인증 (JWT 발급)
TOKEN=$(curl -sf -X POST $BASE/api/v1/auth/token \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"devpass"}' | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])")

# 3) 시나리오 CRUD
curl -sf -X POST $BASE/api/v1/scenarios \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"smoke-scenario","nodes":[]}' | python3 -m json.tool

# 4) Prometheus metrics 포맷 확인
curl -sf $BASE/metrics/prometheus | grep "http_requests_total"
```

### 3-5. 합격 기준

- [ ] pytest integration: 0 failed
- [ ] gRPC Health Check: SERVING
- [ ] smoke_grpc_client: 3/3 calls OK
- [ ] bridge → backend TCP 도달 확인
- [ ] REST API liveness/readyz: `{"status":"ok"}`
- [ ] Prometheus metrics 노출 확인

---

## Phase 4 — E2E 기능 테스트

> 완전한 통화 플로우 시뮬레이션. _test-stub 사용.

### 4-1. 테스트 대상 시나리오

| 번호 | 시나리오 | 검증 포인트 |
|------|----------|------------|
| F-01 | 정상 통화 플로우 | SIP 셋업 → STT → LLM → TTS → 응답 → 종료 |
| F-02 | barge-in (끼어들기) | 고객이 TTS 재생 중 발화 → 즉시 인식 전환 |
| F-03 | VAD silence timeout | 무음 3s → fallback 안내 멘트 → 재시도 |
| F-04 | LLM 실패 → fallback | Groq 타임아웃 → Bedrock Claude 폴백 |
| F-05 | 멀티 테넌트 격리 | tenant-A 세션이 tenant-B 데이터 접근 불가 |
| F-06 | 세션 복구 | Redis 장애 → 재연결 후 세션 상태 복원 |
| F-07 | kill_switch 발동 | `KILL_SWITCH_DEGRADED_VOICE=true` → 폴백 TTS |
| F-08 | Circuit Breaker | 외부 API 5회 연속 실패 → CB OPEN → 즉시 fallback |
| F-09 | 통화 중 drop | 네트워크 끊김 → mid-call drop metric 증가 확인 |
| F-10 | 동시 다중 통화 | 10개 동시 세션 → 간섭 없이 각자 완료 |

### 4-2. _test-stub 기동

```bash
# backend mock stub (vbgw 없이 기능 검증)
cd services/_test-stub
python3 -m pytest . -v   # stub 자체 단위 테스트

# stub 서버 기동
python3 main.py &
STUB_PID=$!
echo "Test stub PID: $STUB_PID"
```

### 4-3. F-01~F-03: 기본 통화 플로우

```bash
cd services/backend

# F-01: 정상 통화 (mock LLM + mock TTS)
pytest tests/ -v -k "e2e or call_flow" -m "not slow"

# F-02: barge-in 검증
# bridge VAD 이벤트 → backend WebSocket 수신 → 세션 interrupt
docker exec vbgw-bridge sh -c \
  'curl -sf http://127.0.0.1:8091/internal/health' | python3 -m json.tool

# F-03: silence timeout
# _test-stub 에 무음 패킷 전송 → fallback 멘트 확인
```

### 4-4. F-04~F-08: Resilience 시나리오

```bash
# F-04: LLM fallback (Groq 비활성화)
GROQ_API_KEY=invalid pytest tests/ -v -k "llm_fallback"

# F-05: 멀티 테넌트 격리
pytest tests/integration/test_auth_e2e.py -v -k "tenant_isolation"

# F-07: kill_switch
KILL_SWITCH_DEGRADED_VOICE=true pytest tests/unit/test_kill_switch.py -v

# F-08: Circuit Breaker OPEN 상태 확인
pytest tests/unit/test_circuit_breaker.py -v -k "open_state"
```

### 4-5. F-10: 동시 세션 테스트

```bash
# Python 비동기 동시 호출 (10 sessions)
python3 - <<'EOF'
import asyncio, httpx, time

BASE = "http://localhost:8000"
SESSIONS = 10

async def one_call(idx):
    async with httpx.AsyncClient() as c:
        r = await c.get(f"{BASE}/api/v1/livez")
        return idx, r.status_code

async def main():
    t0 = time.time()
    results = await asyncio.gather(*[one_call(i) for i in range(SESSIONS)])
    elapsed = time.time() - t0
    ok = sum(1 for _, code in results if code == 200)
    print(f"동시 {SESSIONS}세션: {ok}/{SESSIONS} OK, {elapsed:.2f}s")

asyncio.run(main())
EOF
```

### 4-6. 합격 기준

- [ ] F-01 정상 플로우: 성공
- [ ] F-02 barge-in: VAD 인터럽트 동작 확인
- [ ] F-03 silence timeout: fallback 멘트 발화
- [ ] F-04 LLM fallback: Bedrock 폴백 동작
- [ ] F-05 테넌트 격리: 크로스 테넌트 접근 403
- [ ] F-07 kill_switch: degraded 모드 진입
- [ ] F-08 CB OPEN: fallback 즉시 반환
- [ ] F-10 동시 10세션: 10/10 응답, 오류 없음

---

## Phase 5 — 성능 테스트

> SLO 기준 (`docs/reference/slo.md`) 충족 여부 검증.

### 5-1. SLO 타겟 (테스트 게이트)

| 지표 | SLO 타겟 | 테스트 조건 |
|------|----------|------------|
| backend API 성공률 | ≥ 99.9% | 5분 부하 중 5xx 비율 |
| backend API P95 지연 | ≤ 500ms | HTTP /api/v1/* |
| agentic pipeline P95 | ≤ 2,500ms | E2E 파이프라인 호출 |
| vbgw call setup | ≥ 99.9% | SIP setup 성공률 |
| mid-call drop | < 0.1% | 통화 중 비정상 종료 |

### 5-2. k6 설치 및 스크립트 작성

```bash
# k6 설치 (macOS)
brew install k6
# 또는 Linux
sudo apt-get install k6
```

```javascript
// scripts/perf/k6-backend-load.js
import http from 'k6/http';
import { check, sleep } from 'k6';
import { Rate, Trend } from 'k6/metrics';

const errorRate = new Rate('errors');
const latency   = new Trend('request_latency', true);

export const options = {
  stages: [
    { duration: '1m', target: 10  },   // ramp-up
    { duration: '3m', target: 50  },   // 부하 유지
    { duration: '1m', target: 0   },   // ramp-down
  ],
  thresholds: {
    'http_req_failed':          ['rate<0.001'],   // 99.9% 성공
    'http_req_duration{p:95}':  ['p(95)<500'],    // P95 500ms
    'errors':                   ['rate<0.001'],
  },
};

export default function () {
  const BASE = 'http://localhost:8000';
  const res = http.get(`${BASE}/api/v1/livez`);

  check(res, {
    'status 200': (r) => r.status === 200,
    'latency < 500ms': (r) => r.timings.duration < 500,
  });

  errorRate.add(res.status >= 500);
  latency.add(res.timings.duration);
  sleep(0.1);
}
```

### 5-3. 부하 테스트 실행

```bash
# 스크립트 디렉토리 생성
mkdir -p scripts/perf

# 위 k6 스크립트 저장 후 실행
k6 run scripts/perf/k6-backend-load.js \
  --out json=scripts/perf/results-backend.json

# 결과 요약 출력
k6 run scripts/perf/k6-backend-load.js 2>&1 | tail -30
```

### 5-4. agentic pipeline 성능 테스트

```bash
# agentic 파이프라인 P95 ≤ 2,500ms 검증
cat > /tmp/k6-agentic.js <<'EOF'
import http from 'k6/http';
import { check } from 'k6';

export const options = {
  vus: 5,
  duration: '2m',
  thresholds: {
    'http_req_duration{p:95}': ['p(95)<2500'],
    'http_req_failed':         ['rate<0.005'],
  },
};

export default function () {
  const res = http.post('http://localhost:8000/api/v1/pipeline/invoke',
    JSON.stringify({ tenant: 'perf-test', input: 'hello' }),
    { headers: { 'Content-Type': 'application/json' } });
  check(res, { 'pipeline ok': (r) => r.status < 500 });
}
EOF
k6 run /tmp/k6-agentic.js
```

### 5-5. Prometheus 메트릭 수집 (선택)

```bash
# 테스트 중 Prometheus 메트릭 수집 (로컬 kube 없이 직접 스크랩)
while true; do
  curl -sf http://localhost:8000/metrics/prometheus \
    >> /tmp/metrics-$(date +%s).txt
  sleep 15
done &
METRICS_PID=$!

# 부하 테스트 종료 후
kill $METRICS_PID
grep "http_requests_total" /tmp/metrics-*.txt | tail -5
```

### 5-6. 합격 기준

- [ ] HTTP 성공률: ≥ 99.9% (5xx < 0.1%)
- [ ] HTTP P95 지연: ≤ 500ms
- [ ] agentic pipeline P95: ≤ 2,500ms
- [ ] 50 VUs 부하 중 OOM / panic 없음
- [ ] 부하 종료 후 에러율 0% 복귀

---

## Phase 6 — 배포 테스트

### 6-1. Docker 이미지 빌드 검증

```bash
# backend 이미지 빌드
docker build \
  -f services/backend/Dockerfile \
  -t agentoe-backend:test \
  services/backend/
echo "Backend image: $?"

# vbgw-bridge 이미지 빌드
docker build \
  -f services/vbgw-bridge/Dockerfile \
  -t agentoe-vbgw-bridge:test \
  services/vbgw-bridge/
echo "Bridge image: $?"

# vbgw-orchestrator 이미지 빌드
docker build \
  -f services/vbgw-orchestrator/Dockerfile \
  -t agentoe-vbgw-orchestrator:test \
  services/vbgw-orchestrator/
echo "Orchestrator image: $?"
```

### 6-2. 이미지 보안 스캔

```bash
# trivy 설치 (없으면)
brew install trivy  # macOS
# apt-get install trivy  # Linux

# HIGH/CRITICAL 취약점 확인
trivy image --exit-code 1 --severity HIGH,CRITICAL agentoe-backend:test
trivy image --exit-code 1 --severity HIGH,CRITICAL agentoe-vbgw-bridge:test
```

### 6-3. Helm dry-run (staging)

```bash
# backend staging dry-run
helm upgrade --install agentoe-backend deploy/helm/agentoe-backend \
  -f deploy/helm/agentoe-backend/values/staging.values.yaml \
  --dry-run --debug \
  --namespace agentoe-staging

# vbgw staging dry-run
helm upgrade --install vbgw deploy/helm/vbgw \
  -f deploy/helm/vbgw/values/staging.values.yaml \
  --dry-run --debug \
  --namespace vbgw-staging
```

### 6-4. Canary 배포 시뮬레이션

```bash
# vbgw canary block 검증 (deployment-bridge.yaml 의 canary 설정 확인)
helm template deploy/helm/vbgw \
  -f deploy/helm/vbgw/values/staging.values.yaml \
  --set bridge.canary.enabled=true \
  --set bridge.canary.weight=10 | \
  grep -A 20 "canary"

# canary → stable 롤아웃 시뮬레이션
echo "Stage 1: 10% canary"
echo "Stage 2: 50% canary"
echo "Stage 3: 100% stable (canary 제거)"
# 실제 cutover 는 vbgw-ai-cutover.md runbook 참조
```

### 6-5. docker-compose 통합 전체 기동/종료 검증

```bash
# 전체 기동
SKIP_VBGW=1 AGENTOE_DIR=$(pwd) bash scripts/integration/dev-integration.sh up

# 상태 확인
bash scripts/integration/dev-integration.sh status

# 정상 종료
bash scripts/integration/dev-integration.sh down

# 볼륨 포함 완전 정리 (CI 환경 재현)
docker compose -f docker/compose.backend.yml down -v
docker volume prune -f
```

### 6-6. 합격 기준

- [ ] backend / bridge / orchestrator 이미지 빌드 성공
- [ ] trivy: HIGH/CRITICAL CVE 없음 (또는 허용 목록 예외 처리)
- [ ] Helm dry-run: 오류 없음
- [ ] canary 설정 렌더링 정상
- [ ] docker-compose up → smoke → down 사이클 1회 완료

---

## Phase 7 — 보안 테스트

### 7-1. Python 보안 정적 분석

```bash
cd services/backend

# bandit (Python 보안 lint)
pip install bandit --break-system-packages
bandit -r app/ -ll -f screen
# -ll: medium 이상만 리포트

# safety (의존성 CVE)
pip install safety --break-system-packages
safety check -r <(pip freeze)
```

### 7-2. Go 보안 분석

```bash
# gosec 설치
go install github.com/securego/gosec/v2/cmd/gosec@latest

for svc in services/vbgw-bridge services/vbgw-orchestrator; do
  echo "=== gosec: $svc ==="
  gosec ./... 2>&1 | tail -10
done
```

### 7-3. 인증 경계 테스트

```bash
BASE="http://localhost:8000"

# A) 토큰 없음 → 401
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" $BASE/api/v1/scenarios)
[[ "$HTTP_CODE" == "401" ]] && echo "PASS: 토큰 없음 → 401" || echo "FAIL: $HTTP_CODE"

# B) 만료된 토큰 → 401
EXPIRED_TOKEN="eyJhbGciOiJSUzI1NiJ9.eyJleHAiOjF9.invalid"
HTTP_CODE=$(curl -sf -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer $EXPIRED_TOKEN" $BASE/api/v1/scenarios)
[[ "$HTTP_CODE" == "401" ]] && echo "PASS: 만료 토큰 → 401" || echo "FAIL: $HTTP_CODE"

# C) 잘못된 테넌트 scope → 403
# (실제 테스트는 test_auth_e2e.py 의 tenant_isolation 케이스)

# D) JWKS endpoint 접근 제한 확인
curl -sf $BASE/.well-known/jwks.json | python3 -m json.tool
# keys 배열에 public key 만 노출 확인 (private key X)

# E) Rate limiting (quota) 확인
for i in $(seq 1 20); do
  curl -sf -o /dev/null -w "%{http_code}\n" $BASE/api/v1/livez
done | sort | uniq -c
```

### 7-4. 비밀 노출 확인

```bash
# git history 에 secret 이 없는지 확인
git log --all --oneline --name-only | grep -i "\.env\|secret\|credential" | head -10

# .env 파일이 .gitignore 처리 확인
git ls-files services/backend/.env
# 출력 없으면 OK (추적 안 됨)

# credentials/ 디렉토리 gitignore 확인
git ls-files credentials/
# 실제 자격증명 파일이 추적되면 FAIL
```

### 7-5. 합격 기준

- [ ] bandit: HIGH severity 0건
- [ ] safety: known CVE 없음
- [ ] gosec: G4xx (SQL injection 류) 0건
- [ ] 토큰 없음 → 401 확인
- [ ] 만료 토큰 → 401 확인
- [ ] .env / credentials 가 git 추적 안 됨
- [ ] JWKS에 private key 미노출

---

## 전체 합격 기준 요약 (Go/No-Go 게이트)

| Phase | 핵심 게이트 | 기준 |
|-------|------------|------|
| 0 | 스택 기동 | livez OK, gRPC port open |
| 1 | 정적 분석 | 0 errors (ruff/mypy/vet/helm) |
| 2 | 단위 테스트 | 0 failed, coverage ≥ 70% |
| 3 | 통합 테스트 | smoke 3/3, integration 0 failed |
| 4 | E2E 기능 | F-01~F-08 모두 통과 |
| 5 | 성능 | P95 ≤ 500ms, 성공률 ≥ 99.9% |
| 6 | 배포 | 이미지 빌드 OK, Helm dry-run OK |
| 7 | 보안 | bandit HIGH 0건, 인증 경계 확인 |

**모든 Phase 통과 후 → `feat/monorepo-merge` push → PR → CI green → main merge → cutover Stage A 진행**

---

## 빠른 실행 스크립트

```bash
# 전체 테스트 순서대로 실행 (로컬 자동화)
set -euo pipefail

echo "=== Phase 0: 환경 셋업 ==="
bash scripts/setup-local.sh

echo "=== Phase 1: 정적 분석 ==="
(cd services/backend && ruff check app/ tests/ && mypy app/ --ignore-missing-imports)
for svc in services/vbgw-bridge services/vbgw-orchestrator; do
  (cd "$svc" && go build ./... && go vet ./...)
done
(cd contracts && make gen && git diff --exit-code gen/)

echo "=== Phase 2: 단위 테스트 ==="
(cd services/backend && pytest tests/unit/ -q --tb=short)
(cd services/vbgw-orchestrator && go test ./... -count=1 -race -q)
(cd services/vbgw-bridge && go test ./... -count=1 -race -q)

echo "=== Phase 3: 통합 테스트 ==="
(cd services/backend && pytest tests/integration/ -q --tb=short -m integration)
python3 scripts/integration/smoke_grpc_client.py --addr localhost:50051 --calls 3 --tenant smoke

echo "=== Phase 5: 성능 테스트 ==="
k6 run scripts/perf/k6-backend-load.js

echo ""
echo "✅ 전체 테스트 완료 — staging cutover 진행 가능"
```

---

## 참조

- `docs/reference/slo.md` — SLO 임계값 단일 진실 소스
- `docs/runbook/vbgw-ai-cutover.md` — cutover 4-stage runbook
- `docs/runbook/dev-integration-test.md` — 통합 테스트 상세 가이드
- `scripts/integration/dev-integration.sh` — 통합 환경 자동화
- `scripts/integration/smoke_grpc_client.py` — gRPC smoke client
- `.github/workflows/ci.yml` — CI 파이프라인 (Phase 1-3 자동화)
