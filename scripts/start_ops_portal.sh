#!/usr/bin/env bash
# =============================================================================
# AgentOE 운영포탈 로컬 개발 환경 원클릭 시작 스크립트
#
# 실행 후 브라우저에서 → http://localhost:5174
# 로그인:  admin / admin123  (MFA 없음)
#
# 사용법:
#   ./scripts/start_ops_portal.sh           # 전체 시작
#   ./scripts/start_ops_portal.sh --stop    # 종료 (Docker 포함)
#   ./scripts/start_ops_portal.sh --logs    # 백엔드 로그 보기
#   ./scripts/start_ops_portal.sh --reset   # DB 초기화 후 재시작
# =============================================================================

set -euo pipefail

# ── 색상 ──────────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()     { echo -e "${GREEN}✓${NC}  $*"; }
fail()   { echo -e "${RED}✗${NC}  $*"; }
info()   { echo -e "${CYAN}→${NC}  $*"; }
warn()   { echo -e "${YELLOW}!${NC}  $*"; }
banner() { echo -e "\n${BOLD}${BLUE}$*${NC}\n"; }

# ── 경로 ──────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
COMPOSE_FILE="$REPO_ROOT/docker/compose.ops-portal.dev.yml"
PORTAL_DIR="$REPO_ROOT/services/ops-portal"
BACKEND_DIR="$REPO_ROOT/services/backend"
SEED_SCRIPT="$SCRIPT_DIR/seed_portal_admin.py"

MONGO_URI="mongodb://admin:agentoe_dev_pass@localhost:27017/agentoe?authSource=admin"

# ── 인수 파싱 ─────────────────────────────────────────────────────────────────
ACTION="start"
RESET=false
for arg in "$@"; do
  case $arg in
    --stop)  ACTION="stop"  ;;
    --logs)  ACTION="logs"  ;;
    --reset) RESET=true     ;;
  esac
done

# ── stop ──────────────────────────────────────────────────────────────────────
if [[ $ACTION == "stop" ]]; then
  banner "⏹  운영포탈 종료"
  # Vite 프로세스 종료
  pkill -f "vite.*5174" 2>/dev/null && ok "Vite 개발 서버 종료" || true
  # Docker Compose 종료
  docker compose -f "$COMPOSE_FILE" down
  ok "Docker 서비스 종료 완료"
  exit 0
fi

# ── logs ──────────────────────────────────────────────────────────────────────
if [[ $ACTION == "logs" ]]; then
  docker compose -f "$COMPOSE_FILE" logs -f backend
  exit 0
fi

# ── start ─────────────────────────────────────────────────────────────────────
clear
echo -e "${BOLD}${BLUE}"
echo "  ╔═══════════════════════════════════════╗"
echo "  ║   AgentOE 운영포탈  로컬 개발 환경    ║"
echo "  ╚═══════════════════════════════════════╝"
echo -e "${NC}"

# ── 사전 조건 확인 ────────────────────────────────────────────────────────────
banner "[ 1/5 ]  사전 조건 확인"

command -v docker &>/dev/null   && ok "Docker" \
  || { fail "Docker가 없습니다 → https://docs.docker.com/get-docker/"; exit 1; }

docker info &>/dev/null         && ok "Docker 데몬 실행 중" \
  || { fail "Docker 데몬이 실행되지 않았습니다. Docker Desktop을 시작하세요."; exit 1; }

command -v node &>/dev/null     && ok "Node.js $(node --version)" \
  || { fail "Node.js가 없습니다 → https://nodejs.org"; exit 1; }

command -v python3 &>/dev/null  && ok "Python $(python3 --version | awk '{print $2}')" \
  || { fail "Python3가 없습니다"; exit 1; }

# ── DB 초기화 (--reset) ───────────────────────────────────────────────────────
if $RESET; then
  warn "--reset: MongoDB 볼륨을 삭제합니다"
  docker compose -f "$COMPOSE_FILE" down -v 2>/dev/null || true
  ok "볼륨 삭제 완료 — 깨끗한 상태에서 시작합니다"
fi

# ── Docker Compose 기동 ───────────────────────────────────────────────────────
banner "[ 2/5 ]  인프라 기동 (MongoDB · Redis · Backend)"

info "Docker 이미지 빌드 / 풀 중..."
docker compose -f "$COMPOSE_FILE" build --quiet backend 2>&1 | tail -3

info "컨테이너 시작..."
docker compose -f "$COMPOSE_FILE" up -d

# MongoDB 헬스체크 대기
info "MongoDB 준비 대기 (최대 60초)..."
for i in $(seq 1 12); do
  if docker compose -f "$COMPOSE_FILE" exec -T mongo \
      mongosh --quiet --eval "db.adminCommand('ping').ok" &>/dev/null; then
    ok "MongoDB 준비 완료"
    break
  fi
  [[ $i -eq 12 ]] && { fail "MongoDB 헬스체크 타임아웃"; docker compose -f "$COMPOSE_FILE" logs mongo; exit 1; }
  echo -n "  . "; sleep 5
done

# Backend 헬스체크 대기
info "Backend API 준비 대기 (최대 60초)..."
for i in $(seq 1 12); do
  if curl -sf http://localhost:8000/api/v1/health &>/dev/null; then
    ok "Backend API 준비 완료 (http://localhost:8000)"
    break
  fi
  [[ $i -eq 12 ]] && {
    fail "Backend 헬스체크 타임아웃"
    warn "로그 확인: ./scripts/start_ops_portal.sh --logs"
    exit 1
  }
  echo -n "  . "; sleep 5
done

# ── 관리자 계정 시드 ──────────────────────────────────────────────────────────
banner "[ 3/5 ]  관리자 계정 시드"

info "portal_users 컬렉션에 admin 계정 생성 중..."
python3 "$SEED_SCRIPT" \
  --mongo "$MONGO_URI" \
  --username admin \
  --password "admin123" \
  --role portal:admin \
  --no-mfa \
  2>&1 | grep -E "^\[|다음 단계" || true

ok "admin / admin123  (MFA 비활성) 준비 완료"

# ── Vite 개발 서버 ────────────────────────────────────────────────────────────
banner "[ 4/5 ]  프론트엔드 개발 서버 시작"

cd "$PORTAL_DIR"

if [[ ! -d node_modules ]]; then
  info "npm install (첫 실행 — 약 1분 소요)..."
  npm install --silent
  ok "패키지 설치 완료"
fi

# 이미 실행 중이면 종료 후 재시작
pkill -f "vite.*5174" 2>/dev/null && sleep 1 || true

info "Vite 개발 서버 시작 (port 5174, 백그라운드)..."
VITE_API_TARGET=http://localhost:8000 npm run dev > /tmp/vite-ops-portal.log 2>&1 &
VITE_PID=$!

# Vite 준비 대기
for i in $(seq 1 12); do
  if curl -sf http://localhost:5174 &>/dev/null; then
    ok "Vite 개발 서버 준비 완료 (http://localhost:5174)"
    break
  fi
  [[ $i -eq 12 ]] && {
    fail "Vite 서버 시작 실패. 로그: /tmp/vite-ops-portal.log"
    cat /tmp/vite-ops-portal.log | tail -20
    exit 1
  }
  echo -n "  . "; sleep 3
done

# ── 완료 ──────────────────────────────────────────────────────────────────────
banner "[ 5/5 ]  준비 완료"

echo -e "${BOLD}  브라우저 접속 주소${NC}"
echo -e "  ${GREEN}► 운영포탈${NC}       http://localhost:5174"
echo -e "  ${CYAN}► Backend API${NC}    http://localhost:8000/api/docs"
echo -e "  ${CYAN}► Mongo Express${NC}  docker compose -f docker/compose.ops-portal.dev.yml --profile tools up -d"
echo ""
echo -e "${BOLD}  로그인 정보${NC}"
echo -e "  사용자명: ${YELLOW}admin${NC}"
echo -e "  비밀번호: ${YELLOW}admin123${NC}"
echo -e "  MFA:      없음 (로컬 dev 전용)"
echo ""
echo -e "${BOLD}  관리 명령어${NC}"
echo -e "  로그 보기:   ${CYAN}./scripts/start_ops_portal.sh --logs${NC}"
echo -e "  종료:        ${CYAN}./scripts/start_ops_portal.sh --stop${NC}"
echo -e "  DB 초기화:   ${CYAN}./scripts/start_ops_portal.sh --reset${NC}"
echo ""

# 브라우저 자동 열기 (macOS / Linux)
sleep 1
if command -v open &>/dev/null; then
  open http://localhost:5174
elif command -v xdg-open &>/dev/null; then
  xdg-open http://localhost:5174 &>/dev/null &
fi
