#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# AgentOE 로컬 개발 서버 시작 스크립트
#
# 사용법:  ./start_dev.sh
# 종료:    Ctrl+C  (두 서버 모두 종료됨)
#
# 서비스:
#   • 백엔드 mock 서버  →  http://localhost:8000   (MongoDB/Redis 불필요)
#   • 프론트엔드 Vite   →  http://localhost:5173   (시나리오 빌더 UI)
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")" && pwd)"
FRONTEND="$REPO_ROOT/services/frontend"
MOCK_SERVER="$FRONTEND/tests/e2e/run_test_server.py"
BACKEND_ROOT="$REPO_ROOT/services/backend"

# ── 색상 출력 ────────────────────────────────────────────────────────────────
RESET="\033[0m"
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"

log()  { echo -e "${CYAN}[AgentOE]${RESET} $*"; }
ok()   { echo -e "${GREEN}[  OK  ]${RESET} $*"; }
warn() { echo -e "${YELLOW}[ WARN ]${RESET} $*"; }
err()  { echo -e "${RED}[ ERR  ]${RESET} $*"; }

# ── Python 확인 ──────────────────────────────────────────────────────────────
PYTHON=$(command -v python3 || command -v python || true)
if [ -z "$PYTHON" ]; then
  err "python3 을 찾을 수 없습니다. Python 3.10+ 를 설치하세요."
  exit 1
fi

# ── Node 확인 ────────────────────────────────────────────────────────────────
if ! command -v node &>/dev/null; then
  err "node 를 찾을 수 없습니다. Node.js 18+ 를 설치하세요."
  exit 1
fi

# ── frontend node_modules 확인 ───────────────────────────────────────────────
if [ ! -d "$FRONTEND/node_modules" ]; then
  log "npm install 실행 중..."
  (cd "$FRONTEND" && npm install --silent)
fi

# ── 정리 함수 ────────────────────────────────────────────────────────────────
cleanup() {
  echo ""
  log "서버 종료 중..."
  [ -n "${BACKEND_PID:-}" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "${FRONTEND_PID:-}" ] && kill "$FRONTEND_PID" 2>/dev/null || true
  ok "종료 완료."
  exit 0
}
trap cleanup INT TERM

# ── 백엔드 mock 서버 기동 ─────────────────────────────────────────────────────
log "백엔드 mock 서버 시작 (port 8000) ..."
PYTHONPATH="$BACKEND_ROOT" \
MONGODB_URI="mongodb://localhost:27017/agentoe_dev" \
REDIS_URL="redis://localhost:6379" \
JWT_SECRET="dev-secret-local-only" \
GROQ_API_KEY="gsk_dummy_local_dev" \
GOOGLE_APPLICATION_CREDENTIALS="/tmp/dummy_gcp_agentoe.json" \
  "$PYTHON" "$MOCK_SERVER" --port 8000 &
BACKEND_PID=$!

# GCP credentials 더미 (경로만 필요)
echo '{"type":"service_account","project_id":"agentoe-dev"}' > /tmp/dummy_gcp_agentoe.json

# 백엔드 헬스체크 대기 (최대 15초)
log "백엔드 준비 대기 중..."
for i in $(seq 1 15); do
  if curl -sf http://localhost:8000/api/v1/livez > /dev/null 2>&1; then
    ok "백엔드 준비 완료 → http://localhost:8000"
    break
  fi
  sleep 1
  if [ "$i" -eq 15 ]; then
    err "백엔드 시작 실패. 로그를 확인하세요."
    cleanup
  fi
done

# ── 프론트엔드 Vite dev 서버 기동 ────────────────────────────────────────────
log "프론트엔드 Vite 시작 (port 5173) ..."
(cd "$FRONTEND" && node_modules/.bin/vite --host 0.0.0.0 --port 5173) &
FRONTEND_PID=$!

# 프론트엔드 헬스체크 대기 (최대 15초)
log "프론트엔드 준비 대기 중..."
for i in $(seq 1 15); do
  if curl -sf http://localhost:5173/ > /dev/null 2>&1; then
    break
  fi
  sleep 1
done

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo -e "${GREEN}  🚀 AgentOE Scenario Builder 실행 중${RESET}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${RESET}"
echo ""
echo -e "  📐 시나리오 빌더   →  ${CYAN}http://localhost:5173${RESET}"
echo -e "  🔧 백엔드 API     →  ${CYAN}http://localhost:8000${RESET}"
echo -e "  📖 API 문서        →  ${CYAN}http://localhost:8000/docs${RESET}"
echo ""
echo -e "  종료: ${YELLOW}Ctrl+C${RESET}"
echo ""

# 두 프로세스 모두 살아있는 동안 대기
wait $BACKEND_PID $FRONTEND_PID
