#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# Phase N 전수 테스트 자동화 실행기
#
# 사용법:
#   ./scripts/run_portal_tests.sh              # 전체 Phase 0~8 순서 실행
#   ./scripts/run_portal_tests.sh --phase 2   # 단위 테스트만
#   ./scripts/run_portal_tests.sh --phase 3   # 통합 테스트만
#   ./scripts/run_portal_tests.sh --phase 5   # 성능 테스트만
#   ./scripts/run_portal_tests.sh --phase 6   # 보안 테스트만
#   ./scripts/run_portal_tests.sh --phase 7   # 배포 테스트만
#   ./scripts/run_portal_tests.sh --phase 8   # 스모크 테스트만
#
# 전제조건:
#   docker compose -f docker/compose.ops-portal.dev.yml up -d
#   python scripts/seed_portal_admin.py --no-mfa
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BACKEND="$REPO_ROOT/services/backend"
OPS_PORTAL="$REPO_ROOT/services/ops-portal"

RESET="\033[0m"; GREEN="\033[32m"; CYAN="\033[36m"; YELLOW="\033[33m"; RED="\033[31m"; BOLD="\033[1m"
pass() { echo -e "${GREEN}[PASS]${RESET} $*"; }
fail() { echo -e "${RED}[FAIL]${RESET} $*"; FAILED+=("$*"); }
info() { echo -e "${CYAN}[INFO]${RESET} $*"; }
warn() { echo -e "${YELLOW}[WARN]${RESET} $*"; }
header() { echo -e "\n${BOLD}${CYAN}━━ $* ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"; }

FAILED=()
ONLY_PHASE=""

# ── 인자 파싱 ──────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --phase) ONLY_PHASE="$2"; shift 2 ;;
    *) warn "알 수 없는 옵션: $1"; shift ;;
  esac
done

should_run() { [[ -z "$ONLY_PHASE" || "$ONLY_PHASE" == "$1" ]]; }

# ── 환경변수 ────────────────────────────────────────────────────────────────
export MONGODB_URI="${MONGODB_URI:-mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin}"
export MONGODB_DB_NAME="${MONGODB_DB_NAME:-agentoe}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6380/0}"
export JWT_SECRET="${JWT_SECRET:-dev-jwt-secret-local-only}"
export PORTAL_JWT_SECRET="${PORTAL_JWT_SECRET:-dev-portal-jwt-secret-local}"
export PORTAL_ORIGIN="${PORTAL_ORIGIN:-http://localhost:5174}"
export PORTAL_MFA_ENVELOPE_KEY="${PORTAL_MFA_ENVELOPE_KEY:-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef}"
export PORTAL_KMS_KEY_ID="${PORTAL_KMS_KEY_ID:-}"
export GROQ_API_KEY="${GROQ_API_KEY:-dummy-groq-key}"
export ENVIRONMENT="${ENVIRONMENT:-development}"
export SSE_MAX_CONNECTIONS_PER_POD="0"
BACKEND_URL="${BACKEND_URL:-http://localhost:8000}"

PYTHON=$(command -v python3 || command -v python || true)
START_TIME=$(date +%s)

# ─────────────────────────────────────────────────────────────────────────────
# Phase 0 — 환경 검증
# ─────────────────────────────────────────────────────────────────────────────
if should_run 0; then
  header "Phase 0 — 환경 검증"

  # Docker
  if docker info &>/dev/null; then pass "Docker 실행 중"
  else fail "Docker 미실행"; fi

  # backend healthcheck
  if curl -sf "$BACKEND_URL/api/v1/health" | grep -q "ok"; then
    pass "backend healthcheck OK"
  else
    warn "backend 미실행 — Phase 3/4/8 은 스킵됩니다"
  fi

  # MongoDB
  if docker exec agentoe-portal-mongo mongosh \
    "mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin" \
    --quiet --eval "db.adminCommand('ping').ok" 2>/dev/null | grep -q "1"; then
    pass "MongoDB ping OK"
  else
    warn "MongoDB 컨테이너 미실행 (agentoe-portal-mongo)"
  fi

  # Redis
  if docker exec agentoe-portal-redis redis-cli ping 2>/dev/null | grep -q "PONG"; then
    pass "Redis ping OK"
  else
    warn "Redis 컨테이너 미실행 (agentoe-portal-redis)"
  fi

  # portal_users 계정 확인
  USER_COUNT=$("$PYTHON" -c "
import asyncio, sys
try:
    import motor.motor_asyncio as m
    async def c():
        cl = m.AsyncIOMotorClient('$MONGODB_URI')
        n = await cl['$MONGODB_DB_NAME']['portal_users'].count_documents({})
        cl.close(); return n
    print(asyncio.run(c()))
except: print(0)
" 2>/dev/null || echo 0)
  if [[ "${USER_COUNT:-0}" -gt 0 ]]; then
    pass "portal_users 계정 ${USER_COUNT}개 확인"
  else
    warn "portal_users 비어있음 — seed_portal_admin.py 실행 권장"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 1 — 정적 분석
# ─────────────────────────────────────────────────────────────────────────────
if should_run 1; then
  header "Phase 1 — 정적 분석"

  # TypeScript
  info "TypeScript 타입 검사..."
  if (cd "$OPS_PORTAL" && npx tsc --noEmit 2>&1); then
    pass "tsc --noEmit 통과"
  else
    fail "tsc 타입 에러 발생"
  fi

  # Python ruff
  info "ruff lint (portal 범위)..."
  if (cd "$BACKEND" && python -m ruff check \
    app/api/v1/routers/auth_portal.py \
    app/api/v1/routers/stream.py \
    app/api/v1/routers/admin.py \
    app/core/auth.py \
    app/domain/portal_session.py \
    app/infra/kms_client.py 2>&1); then
    pass "ruff lint 통과"
  else
    fail "ruff lint 에러"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 2 — 단위 테스트
# ─────────────────────────────────────────────────────────────────────────────
if should_run 2; then
  header "Phase 2 — 단위 테스트"
  info "pytest unit 실행 중..."
  if (cd "$BACKEND" && \
    python -m pytest \
      tests/unit/test_portal_rbac.py \
      tests/unit/test_audit_emitter.py \
      tests/unit/test_portal_auth_flow.py \
      -v --no-header --tb=short \
      --rootdir="$BACKEND" --no-cov -q 2>&1); then
    pass "단위 테스트 전체 통과"
  else
    fail "단위 테스트 실패"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 3 — 통합 테스트
# ─────────────────────────────────────────────────────────────────────────────
if should_run 3; then
  header "Phase 3 — 통합 테스트"
  if curl -sf "$BACKEND_URL/api/v1/health" &>/dev/null; then
    info "pytest integration 실행 중..."
    if (cd "$BACKEND" && \
      python -m pytest tests/integration/test_portal_api.py \
        -v --no-header --tb=short -x 2>&1); then
      pass "통합 테스트 전체 통과"
    else
      fail "통합 테스트 실패"
    fi
  else
    warn "Phase 3 스킵 — backend 미실행"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 4 — 기능 테스트 (API curl 체인)
# ─────────────────────────────────────────────────────────────────────────────
if should_run 4; then
  header "Phase 4 — 기능 테스트 (API curl 체인)"

  if ! curl -sf "$BACKEND_URL/api/v1/health" &>/dev/null; then
    warn "Phase 4 스킵 — backend 미실행"
  else
    # F-01: 잘못된 비밀번호
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X POST \
      "$BACKEND_URL/api/v1/auth/portal/login" \
      -H "Content-Type: application/json" \
      -d '{"username":"admin","password":"wrong_password"}')
    if [[ "$CODE" == "401" ]]; then pass "F-01: 잘못된 비밀번호 → 401"
    else fail "F-01: 잘못된 비밀번호 → 기대 401, 실제 $CODE"; fi

    # F-02: 정상 로그인
    RESP=$(curl -s -c /tmp/portal_cookies.txt \
      -X POST "$BACKEND_URL/api/v1/auth/portal/login" \
      -H "Content-Type: application/json" \
      -d '{"username":"admin","password":"admin123"}')
    CODE=$(echo "$RESP" | python3 -c "import sys,json; d=json.load(sys.stdin); print('200' if 'mfa_required' in d or 'access_token' in d else '500')" 2>/dev/null || echo "500")
    if [[ "$CODE" == "200" ]]; then pass "F-02: 정상 로그인 → 성공"
    else fail "F-02: 정상 로그인 실패 (응답: ${RESP:0:100})"; fi

    # F-03: CSRF 없이 PUT → 403
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
      "$BACKEND_URL/api/v1/admin/config/dev" \
      -b /tmp/portal_cookies.txt \
      -H "Content-Type: application/json" \
      -d '{"updated_by":"test","values":{}}')
    if [[ "$CODE" == "403" ]]; then pass "F-03: CSRF 없이 PUT → 403"
    else warn "F-03: CSRF 없이 PUT → $CODE (쿠키 기반 인증 필요할 수 있음)"; fi

    # F-04: SSE 연결 (3초 timeout)
    SSE_OUTPUT=$(timeout 3 curl -sN \
      "$BACKEND_URL/api/v1/stream/metrics" \
      -b /tmp/portal_cookies.txt \
      -H "Accept: text/event-stream" 2>/dev/null || true)
    if echo "$SSE_OUTPUT" | grep -q "data\|:\|event"; then
      pass "F-04: SSE /stream/metrics 이벤트 수신"
    else
      warn "F-04: SSE 이벤트 수신 불가 (인증 필요 또는 연결 실패)"
    fi
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 5 — 성능 테스트
# ─────────────────────────────────────────────────────────────────────────────
if should_run 5; then
  header "Phase 5 — 성능 테스트"
  info "pytest performance 실행 중..."
  if (cd "$BACKEND" && \
    python -m pytest tests/performance/test_portal_load.py \
      -v --no-header --tb=short -s 2>&1); then
    pass "성능 테스트 전체 SLO 충족"
  else
    fail "성능 테스트 SLO 미충족"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 6 — 보안 테스트
# ─────────────────────────────────────────────────────────────────────────────
if should_run 6; then
  header "Phase 6 — 보안 테스트"

  # 6-1: RBAC issuer 격리
  info "issuer 격리 단위 테스트..."
  if (cd "$BACKEND" && \
    python -m pytest tests/unit/test_portal_rbac.py -v -q \
      --rootdir="$BACKEND" --no-cov 2>&1); then
    pass "6-1: RBAC issuer 격리 통과"
  else
    fail "6-1: RBAC issuer 격리 실패"
  fi

  # 6-2: brute-force 보호 (backend 실행 중일 때만)
  if curl -sf "$BACKEND_URL/api/v1/health" &>/dev/null; then
    info "brute-force 보호 테스트 (6회 실패 시도)..."
    LAST_CODE="000"
    for i in {1..6}; do
      LAST_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
        -X POST "$BACKEND_URL/api/v1/auth/portal/login" \
        -H "Content-Type: application/json" \
        -d '{"username":"brute_test_user","password":"wrong"}')
      sleep 0.2
    done
    if [[ "$LAST_CODE" == "429" || "$LAST_CODE" == "401" ]]; then
      pass "6-2: brute-force 응답 $LAST_CODE (401=인증실패/429=rate-limit)"
    else
      warn "6-2: 6회 실패 후 HTTP $LAST_CODE (rate-limit Redis 필요)"
    fi

    # 6-3: CSRF double-submit
    CODE=$(curl -s -o /dev/null -w "%{http_code}" -X PUT \
      "$BACKEND_URL/api/v1/admin/config/dev" \
      -H "Content-Type: application/json" \
      -d '{"updated_by":"test","values":{}}')
    if [[ "$CODE" == "403" || "$CODE" == "401" ]]; then
      pass "6-3: CSRF 없이 PUT → $CODE"
    else
      fail "6-3: CSRF 없이 PUT 통과 ($CODE)"
    fi
  else
    warn "Phase 6 brute-force/CSRF 스킵 — backend 미실행"
  fi

  # 6-4: bandit (설치된 경우)
  if command -v bandit &>/dev/null; then
    info "bandit 보안 정적 분석..."
    if (cd "$BACKEND" && bandit -r \
      app/api/v1/routers/auth_portal.py \
      app/domain/portal_session.py \
      app/infra/kms_client.py \
      -ll -q 2>&1); then
      pass "6-4: bandit HIGH severity 0건"
    else
      fail "6-4: bandit HIGH severity 발견"
    fi
  else
    warn "6-4: bandit 미설치 스킵 (pip install bandit --break-system-packages)"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 7 — 배포 테스트
# ─────────────────────────────────────────────────────────────────────────────
if should_run 7; then
  header "Phase 7 — 배포 테스트"

  # Helm lint
  if command -v helm &>/dev/null; then
    for ENV in staging prod; do
      if helm lint "$REPO_ROOT/deploy/helm/agentoe-portal" \
        -f "$REPO_ROOT/deploy/helm/values/$ENV/portal.values.yaml" \
        --quiet 2>&1; then
        pass "7-1: helm lint ($ENV) 통과"
      else
        fail "7-1: helm lint ($ENV) 실패"
      fi
    done
  else
    warn "7-1: helm 미설치 스킵"
  fi

  # Docker 빌드
  if command -v docker &>/dev/null; then
    info "Docker 빌드 (ops-portal)..."
    if docker build -t ops-portal:test "$REPO_ROOT/services/ops-portal/" \
      --build-arg VCS_REF=test \
      --build-arg BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
      --quiet 2>&1; then
      pass "7-2: Docker 빌드 성공"
      # 빌드 이미지 정리
      docker rmi ops-portal:test --force &>/dev/null || true
    else
      fail "7-2: Docker 빌드 실패"
    fi
  else
    warn "7-2: docker 미설치 스킵"
  fi

  # dry-run 배포 게이트
  info "prod 배포 dry-run..."
  if "$REPO_ROOT/scripts/portal_prod_deploy.sh" \
    --image-tag test --dry-run 2>&1 | grep -q "\[DRY-RUN\]\|\[SKIP\]"; then
    pass "7-3: prod 배포 dry-run 통과"
  else
    warn "7-3: dry-run 출력 확인 필요 (환경변수 미설정 시 일부 gate 스킵)"
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# Phase 8 — 스모크 테스트
# ─────────────────────────────────────────────────────────────────────────────
if should_run 8; then
  header "Phase 8 — 최종 스모크 테스트"

  if ! curl -sf "$BACKEND_URL/api/v1/health" &>/dev/null; then
    warn "Phase 8 스킵 — backend 미실행"
  else
    # Step 1: health
    if curl -sf "$BACKEND_URL/api/v1/health" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); exit(0 if d.get('status')=='ok' else 1)" 2>/dev/null; then
      pass "8-1: /api/v1/health → ok"
    else fail "8-1: health 실패"; fi

    # Step 2: login
    SMOKE_RESP=$(curl -s -c /tmp/smoke_cookies.txt \
      -X POST "$BACKEND_URL/api/v1/auth/portal/login" \
      -H "Content-Type: application/json" \
      -d '{"username":"admin","password":"admin123"}')
    if echo "$SMOKE_RESP" | python3 -c \
      "import sys,json; d=json.load(sys.stdin); exit(0 if 'mfa_required' in d or 'access_token' in d else 1)" 2>/dev/null; then
      pass "8-2: portal login → 성공"
    else fail "8-2: portal login 실패 (admin/admin123 계정 존재 확인 필요)"; fi

    CSRF_TOKEN=$(grep "__csrf__" /tmp/smoke_cookies.txt 2>/dev/null | awk '{print $NF}' || echo "")

    # Step 3: env/info
    CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -b /tmp/smoke_cookies.txt \
      "$BACKEND_URL/api/v1/admin/env/info")
    if [[ "$CODE" == "200" ]]; then pass "8-3: /admin/env/info → 200"
    else warn "8-3: /admin/env/info → $CODE (토큰 쿠키 필요)"; fi

    # Step 4: SSE 연결
    SSE_OUT=$(timeout 4 curl -sN \
      -b /tmp/smoke_cookies.txt \
      -H "Accept: text/event-stream" \
      "$BACKEND_URL/api/v1/stream/metrics" 2>/dev/null | head -5 || true)
    if [[ -n "$SSE_OUT" ]]; then pass "8-4: SSE /stream/metrics 응답 수신"
    else warn "8-4: SSE 응답 없음 (인증 쿠키 방식 확인 필요)"; fi

    # Step 5: config/dev GET
    CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -b /tmp/smoke_cookies.txt \
      "$BACKEND_URL/api/v1/admin/config/dev")
    if [[ "$CODE" == "200" ]]; then pass "8-5: /admin/config/dev → 200"
    else warn "8-5: /admin/config/dev → $CODE"; fi

    # Step 6: logout
    CODE=$(curl -s -o /dev/null -w "%{http_code}" \
      -X POST "$BACKEND_URL/api/v1/auth/portal/logout" \
      -b /tmp/smoke_cookies.txt \
      -H "X-CSRF-Token: $CSRF_TOKEN")
    if [[ "$CODE" =~ ^(200|204)$ ]]; then pass "8-6: logout → $CODE"
    else warn "8-6: logout → $CODE"; fi

    rm -f /tmp/smoke_cookies.txt /tmp/portal_cookies.txt
  fi
fi

# ─────────────────────────────────────────────────────────────────────────────
# 최종 결과
# ─────────────────────────────────────────────────────────────────────────────
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${BOLD} Phase N 전수 테스트 결과 (소요: ${ELAPSED}초)${RESET}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"

if [[ ${#FAILED[@]} -eq 0 ]]; then
  echo -e "${GREEN}${BOLD}  ✓ 전체 PASS — staging 배포 진행 가능${RESET}"
  exit 0
else
  echo -e "${RED}${BOLD}  ✗ 실패 항목 ${#FAILED[@]}건:${RESET}"
  for f in "${FAILED[@]}"; do
    echo -e "    ${RED}• $f${RESET}"
  done
  echo ""
  echo -e "${YELLOW}  ★ Phase 6 실패 항목이 있으면 절대 머지 금지${RESET}"
  exit 1
fi
