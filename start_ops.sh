#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# AgentOE 통합 운영 포탈 — 로컬 개발 시작 스크립트 (Phase N)
#
# 전제조건 (인프라 먼저):
#   docker compose -f docker/compose.ops-portal.dev.yml up -d
#
# 사용법:
#   ./start_ops.sh            # backend + ops-portal Vite 동시 기동
#   ./start_ops.sh --no-backend   # Vite 만 기동 (backend 를 직접 띄울 때)
#
# 서비스:
#   • backend (FastAPI + --reload)  →  http://localhost:8000
#   • ops-portal (Vite dev server)  →  http://localhost:5174
#
# backend 는 services/backend 를 소스 mount 해 --reload 로 실행합니다.
# docker compose.ops-portal.dev.yml 을 사용할 경우 이 스크립트의 backend 기동은
# 중복이므로 --no-backend 옵션을 사용하세요.
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
BACKEND="$REPO_ROOT/services/backend"
OPS_PORTAL="$REPO_ROOT/services/ops-portal"

RESET="\033[0m"; GREEN="\033[32m"; CYAN="\033[36m"; YELLOW="\033[33m"; RED="\033[31m"
log()  { echo -e "${CYAN}[OpsPortal]${RESET} $*"; }
ok()   { echo -e "${GREEN}[  OK  ]${RESET} $*"; }
warn() { echo -e "${YELLOW}[ WARN ]${RESET} $*"; }
err()  { echo -e "${RED}[ ERR  ]${RESET} $*"; }

# ── 옵션 파싱 ────────────────────────────────────────────────────────────────
NO_BACKEND=false
for arg in "$@"; do
  case "$arg" in
    --no-backend) NO_BACKEND=true ;;
    *) warn "알 수 없는 옵션: $arg (무시)" ;;
  esac
done

# ── 환경변수 기본값 (docker compose 없이 직접 실행 시) ────────────────────────
export MONGODB_URI="${MONGODB_URI:-mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin}"
export MONGODB_DB_NAME="${MONGODB_DB_NAME:-agentoe}"
export REDIS_URL="${REDIS_URL:-redis://localhost:6380/0}"
export ENVIRONMENT="${ENVIRONMENT:-development}"
export LOG_LEVEL="${LOG_LEVEL:-info}"
export JWT_SECRET="${JWT_SECRET:-dev-jwt-secret-local-only}"
export PORTAL_ORIGIN="${PORTAL_ORIGIN:-http://localhost:5174}"
export PORTAL_JWT_SECRET="${PORTAL_JWT_SECRET:-dev-portal-jwt-secret-local}"
export PORTAL_JWT_EXPIRE_MINUTES="${PORTAL_JWT_EXPIRE_MINUTES:-30}"
export PORTAL_REFRESH_EXPIRE_HOURS="${PORTAL_REFRESH_EXPIRE_HOURS:-24}"
# 로컬 전용 MFA envelope key (64자 hex = 32bytes). 운영에 절대 사용 금지.
export PORTAL_MFA_ENVELOPE_KEY="${PORTAL_MFA_ENVELOPE_KEY:-0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef}"
export PORTAL_KMS_KEY_ID="${PORTAL_KMS_KEY_ID:-}"
export ALERTMANAGER_URL="${ALERTMANAGER_URL:-http://localhost:9093}"
export PROMETHEUS_URL="${PROMETHEUS_URL:-http://localhost:9090}"
export SSE_MAX_CONNECTIONS_PER_POD="${SSE_MAX_CONNECTIONS_PER_POD:-0}"
export GROQ_API_KEY="${GROQ_API_KEY:-dummy-groq-key-for-dev}"

# ── Python / Node 확인 ────────────────────────────────────────────────────────
PYTHON=$(command -v python3 2>/dev/null || command -v python 2>/dev/null || true)
if [ -z "$PYTHON" ]; then
  err "python3 를 찾을 수 없습니다. Python 3.11+ 를 설치하세요."
  exit 1
fi
if ! command -v node &>/dev/null; then
  err "node 를 찾을 수 없습니다. Node.js 18+ 를 설치하세요."
  exit 1
fi

# ── 인프라(MongoDB / Redis) 기동 확인 ────────────────────────────────────────
log "MongoDB 연결 확인 중..."
if ! "$PYTHON" -c "
import sys, urllib.parse, socket
try:
    h = urllib.parse.urlparse('$MONGODB_URI').hostname or 'localhost'
    s = socket.create_connection((h, 27017), timeout=2); s.close()
except Exception as e:
    print(f'MongoDB 연결 불가: {e}', file=sys.stderr); sys.exit(1)
" 2>&1; then
  err "MongoDB 에 연결할 수 없습니다."
  echo ""
  echo "  먼저 인프라를 기동하세요:"
  echo "    docker compose -f docker/compose.ops-portal.dev.yml up -d"
  exit 1
fi
ok "MongoDB 연결 OK"

log "Redis 연결 확인 중..."
REDIS_HOST="${REDIS_URL#redis://}"
REDIS_HOST="${REDIS_HOST%%/*}"
REDIS_HOST="${REDIS_HOST%%:*}"
REDIS_PORT="6380"
if ! "$PYTHON" -c "
import socket
try:
    s = socket.create_connection(('$REDIS_HOST', $REDIS_PORT), timeout=2); s.close()
except Exception as e:
    print(f'Redis 연결 불가: {e}'); import sys; sys.exit(1)
" 2>&1; then
  warn "Redis 에 연결할 수 없습니다 — SSE / 리더 선출 기능이 제한됩니다."
fi

# ── portal admin 계정 존재 확인 ──────────────────────────────────────────────
log "portal_users 컬렉션 확인 중..."
USER_COUNT=$("$PYTHON" -c "
import asyncio, sys
try:
    import motor.motor_asyncio as motor
    async def check():
        c = motor.AsyncIOMotorClient('$MONGODB_URI', serverSelectionTimeoutMS=3000)
        n = await c['$MONGODB_DB_NAME']['portal_users'].count_documents({})
        c.close()
        return n
    print(asyncio.run(check()))
except Exception as e:
    print(0)
" 2>/dev/null || echo 0)

if [ "${USER_COUNT:-0}" -eq 0 ]; then
  warn "portal_users 컬렉션이 비어 있습니다."
  echo ""
  echo "  초기 admin 계정을 먼저 생성하세요:"
  echo "    python scripts/seed_portal_admin.py --no-mfa"
  echo "  (MFA 없이 빠르게 테스트하려면 --no-mfa 권장)"
  echo ""
  read -rp "  지금 바로 admin 계정을 생성하시겠습니까? (Y/n) " ans
  ans="${ans:-Y}"
  if [[ "$ans" =~ ^[Yy] ]]; then
    "$PYTHON" "$REPO_ROOT/scripts/seed_portal_admin.py" --no-mfa
  fi
fi

# ── ops-portal npm install ────────────────────────────────────────────────────
if [ ! -d "$OPS_PORTAL/node_modules" ]; then
  log "npm install 실행 중 (ops-portal)..."
  (cd "$OPS_PORTAL" && npm install --silent)
fi

# ── 정리 함수 ─────────────────────────────────────────────────────────────────
API_PID=""
WEB_PID=""
cleanup() {
  echo ""
  log "서버 종료 중..."
  [ -n "$API_PID" ] && kill "$API_PID" 2>/dev/null || true
  [ -n "$WEB_PID" ] && kill "$WEB_PID" 2>/dev/null || true
  ok "종료 완료."
  exit 0
}
trap cleanup INT TERM

# ── backend 기동 (port 8000, --reload) ───────────────────────────────────────
if [ "$NO_BACKEND" = false ]; then
  log "backend 시작 (port 8000, --reload) ..."
  (
    cd "$BACKEND"
    PYTHONPATH="$BACKEND" \
      "$PYTHON" -m uvicorn app.main:app \
        --host 127.0.0.1 --port 8000 \
        --reload \
        --log-level "${LOG_LEVEL:-info}"
  ) &
  API_PID=$!

  log "backend 준비 대기 중 (최대 20초)..."
  for i in $(seq 1 20); do
    if curl -sf http://localhost:8000/api/v1/health > /dev/null 2>&1; then
      ok "backend 준비 완료 → http://localhost:8000"
      break
    fi
    sleep 1
    if [ "$i" -eq 20 ]; then
      err "backend 시작 실패 — 로그를 확인하세요."
      cleanup
    fi
  done
else
  log "--no-backend: backend 기동 생략 (http://localhost:8000 이 이미 실행 중인지 확인)"
fi

# ── ops-portal Vite (port 5174) ───────────────────────────────────────────────
log "ops-portal Vite 시작 (port 5174) ..."
(cd "$OPS_PORTAL" && node_modules/.bin/vite --port 5174 --host 127.0.0.1) &
WEB_PID=$!

for i in $(seq 1 20); do
  if curl -sf http://localhost:5174/ > /dev/null 2>&1; then break; fi
  sleep 1
done

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  AgentOE 통합 운영 포탈 — 로컬 개발 서버 실행 중${RESET}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "  운영 포탈         →  ${CYAN}http://localhost:5174${RESET}"
echo -e "  Backend API       →  ${CYAN}http://localhost:8000/api/v1${RESET}"
echo -e "  Swagger UI        →  ${CYAN}http://localhost:8000/docs${RESET}"
echo ""
echo -e "  로그인:  admin / admin123  (seed_portal_admin.py 기본값)"
echo ""
echo -e "  (시나리오 저작 도구: ${CYAN}http://localhost:5173${RESET}  →  ./start_dev.sh)"
echo ""
echo -e "  종료: ${YELLOW}Ctrl+C${RESET}"
echo ""

wait
