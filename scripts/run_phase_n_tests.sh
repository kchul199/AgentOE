#!/usr/bin/env bash
# =============================================================================
# Phase N — 운영포탈 전수 테스트 로컬 실행 스크립트
#
# 외부 서비스 불필요:
#   - MongoDB  → mongomock-motor (in-memory)
#   - Redis    → fakeredis        (in-memory)
#   - gRPC     → AsyncMock        (patched)
#
# 사용법:
#   chmod +x scripts/run_phase_n_tests.sh
#   ./scripts/run_phase_n_tests.sh            # 전체 실행
#   ./scripts/run_phase_n_tests.sh --ts-only  # TypeScript 타입 검사만
#   ./scripts/run_phase_n_tests.sh --unit     # 단위 테스트만
#   ./scripts/run_phase_n_tests.sh --int      # 통합 테스트만
#   ./scripts/run_phase_n_tests.sh --perf     # 성능 테스트만
#   ./scripts/run_phase_n_tests.sh --skip-ts  # TypeScript 건너뜀
# =============================================================================

set -euo pipefail

# ── 색상 출력 ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC}  $*"; }
fail() { echo -e "${RED}✗${NC}  $*"; }
info() { echo -e "${CYAN}→${NC}  $*"; }
warn() { echo -e "${YELLOW}!${NC}  $*"; }
banner() { echo -e "\n${BOLD}${BLUE}━━ $* ━━${NC}\n"; }

# ── 경로 설정 ──────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
BACKEND_DIR="$REPO_ROOT/services/backend"
PORTAL_DIR="$REPO_ROOT/services/ops-portal"

# ── 인수 파싱 ──────────────────────────────────────────────────────────────
RUN_TS=true; RUN_UNIT=true; RUN_INT=true; RUN_PERF=true

for arg in "$@"; do
  case $arg in
    --ts-only)  RUN_UNIT=false; RUN_INT=false; RUN_PERF=false ;;
    --skip-ts)  RUN_TS=false ;;
    --unit)     RUN_TS=false; RUN_INT=false; RUN_PERF=false; RUN_UNIT=true ;;
    --int)      RUN_TS=false; RUN_UNIT=false; RUN_PERF=false; RUN_INT=true ;;
    --perf)     RUN_TS=false; RUN_UNIT=false; RUN_INT=false; RUN_PERF=true ;;
    --help|-h)
      sed -n '/^# 사용법/,/^# ====/p' "$0" | grep -v '^#' || true
      grep '^\s*--' "$0" | head -10
      exit 0 ;;
  esac
done

# 결과 추적
PASS=(); FAIL=(); SKIP=()
record_pass() { PASS+=("$1"); ok "$1"; }
record_fail() { FAIL+=("$1"); fail "$1"; }
record_skip() { SKIP+=("$1"); warn "$1 — SKIP"; }

# ── 1. 사전 조건 확인 ─────────────────────────────────────────────────────
banner "사전 조건 확인"

# Python 버전 — 3.11+ 권장, 3.10도 동작하지만 타입 힌트 일부 미지원
PY=$(python3 --version 2>&1 | awk '{print $2}')
PY_MAJOR=$(echo "$PY" | cut -d. -f1)
PY_MINOR=$(echo "$PY" | cut -d. -f2)
if [[ $PY_MAJOR -ge 3 && $PY_MINOR -ge 11 ]]; then
  ok "Python $PY"
elif [[ $PY_MAJOR -ge 3 && $PY_MINOR -ge 10 ]]; then
  warn "Python $PY (권장: 3.11+) — 테스트는 실행 가능"
else
  fail "Python $PY — 3.10+ 필요. pyenv 또는 brew로 업그레이드 후 재시도"
  exit 1
fi

# pip
python3 -m pip --version &>/dev/null && ok "pip 사용 가능" || { fail "pip 없음"; exit 1; }

# Node (TypeScript 검사 시)
if $RUN_TS; then
  node --version &>/dev/null && ok "Node $(node --version)" \
    || { warn "Node.js 없음 — TypeScript 검사 건너뜀"; RUN_TS=false; }
fi

# ── 2. Python 의존성 설치 ─────────────────────────────────────────────────
banner "Python 의존성 설치"
cd "$BACKEND_DIR"

info "pip install -e '.[dev]' (editable + dev extras)"
python3 -m pip install -e ".[dev]" -q

info "통합 테스트 전용 패키지 확인"
python3 -m pip install \
  "mongomock-motor>=0.0.21" \
  "fakeredis[aioredis]>=2.23.0" \
  -q

ok "Python 의존성 완료"

# ── 3. TypeScript 타입 검사 ───────────────────────────────────────────────
if $RUN_TS; then
  banner "Phase 1 — TypeScript 정적 분석"
  cd "$PORTAL_DIR"

  if [[ ! -d node_modules ]]; then
    info "npm install (최초 실행)"
    npm install --silent
  fi

  info "tsc --noEmit"
  if npx tsc --noEmit 2>&1; then
    record_pass "TypeScript — 타입 오류 없음"
  else
    record_fail "TypeScript — 타입 오류 발생 (위 출력 확인)"
  fi
  cd "$BACKEND_DIR"
fi

# ── 4. 환경변수 기본값 ────────────────────────────────────────────────────
export MONGODB_URI="${MONGODB_URI:-mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin}"
export MONGODB_DB_NAME="${MONGODB_DB_NAME:-agentoe}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6380/0}"
export PORTAL_MFA_ENVELOPE_KEY="${PORTAL_MFA_ENVELOPE_KEY:-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef}"
export PORTAL_JWT_SECRET="${PORTAL_JWT_SECRET:-dev-portal-jwt-secret-local}"
export PORTAL_ORIGIN="${PORTAL_ORIGIN:-http://localhost:5174}"
export JWT_SECRET="${JWT_SECRET:-dev-jwt-secret-local}"
export GROQ_API_KEY="${GROQ_API_KEY:-dummy-groq-key}"
export ENVIRONMENT="${ENVIRONMENT:-development}"

# ── 5. 단위 테스트 ───────────────────────────────────────────────────────
if $RUN_UNIT; then
  banner "Phase 2 — 단위 테스트 (외부 의존성 없음)"
  cd "$BACKEND_DIR"

  echo ""
  info "▸ Portal 인증 흐름 (U-01 ~ U-08)"
  if python3 -m pytest tests/unit/test_portal_auth_flow.py \
      --no-cov --tb=short -q 2>&1; then
    record_pass "단위 테스트: test_portal_auth_flow.py"
  else
    record_fail "단위 테스트: test_portal_auth_flow.py"
  fi

  echo ""
  info "▸ Portal RBAC 권한 분리"
  if python3 -m pytest tests/unit/test_portal_rbac.py \
      --no-cov --tb=short -q 2>&1; then
    record_pass "단위 테스트: test_portal_rbac.py"
  else
    record_fail "단위 테스트: test_portal_rbac.py"
  fi
fi

# ── 6. 통합 테스트 ───────────────────────────────────────────────────────
if $RUN_INT; then
  banner "Phase 3 — 통합 테스트 (mongomock + fakeredis)"
  cd "$BACKEND_DIR"

  info "P-INT-01 ~ P-INT-10 실행"
  if python3 -m pytest tests/integration/test_portal_api.py \
      --no-cov --tb=short -q 2>&1; then
    record_pass "통합 테스트: test_portal_api.py (P-INT-01~10)"
  else
    record_fail "통합 테스트: test_portal_api.py"
  fi
fi

# ── 7. 성능 테스트 ───────────────────────────────────────────────────────
if $RUN_PERF; then
  banner "Phase 5 — 성능 테스트 (ASGI in-process)"
  cd "$BACKEND_DIR"

  info "L-01 ~ L-05 실행 (처리량 / 레이턴시 / 동시성)"
  if python3 -m pytest tests/performance/test_portal_load.py \
      --no-cov --tb=short -q 2>&1; then
    record_pass "성능 테스트: test_portal_load.py (L-01~05)"
  else
    record_fail "성능 테스트: test_portal_load.py"
  fi
fi

# ── 8. 최종 요약 ─────────────────────────────────────────────────────────
banner "테스트 결과 요약"

TOTAL=$(( ${#PASS[@]} + ${#FAIL[@]} + ${#SKIP[@]} ))

for p in "${PASS[@]}"; do echo -e "  ${GREEN}PASS${NC}  $p"; done
for s in "${SKIP[@]}"; do echo -e "  ${YELLOW}SKIP${NC}  $s"; done
for f in "${FAIL[@]}"; do echo -e "  ${RED}FAIL${NC}  $f"; done

echo ""
echo -e "${BOLD}총 ${TOTAL}개 suite:  ${GREEN}${#PASS[@]} 통과${NC}  ${YELLOW}${#SKIP[@]} 스킵${NC}  ${RED}${#FAIL[@]} 실패${NC}${BOLD}${NC}"

if [[ ${#FAIL[@]} -gt 0 ]]; then
  echo ""
  warn "실패한 suite 의 상세 로그를 확인하세요."
  exit 1
else
  echo ""
  ok "모든 테스트 통과 ✓"
fi
