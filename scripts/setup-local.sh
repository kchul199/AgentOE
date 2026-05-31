#!/usr/bin/env bash
# AgentOE 로컬 개발 환경 자동 설정 스크립트
# 사용: bash scripts/setup-local.sh
set -euo pipefail

BOLD='\033[1m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()    { echo -e "${GREEN}[INFO]${NC} $1"; }
warn()    { echo -e "${YELLOW}[WARN]${NC} $1"; }
error()   { echo -e "${RED}[ERROR]${NC} $1"; exit 1; }
header()  { echo -e "\n${BOLD}=== $1 ===${NC}"; }

# ── 사전 요구사항 확인 ───────────────────────────────────────────────────
header "사전 요구사항 확인"

command -v docker    >/dev/null 2>&1 || error "Docker가 설치되어 있지 않습니다."
command -v python3   >/dev/null 2>&1 || error "Python3가 설치되어 있지 않습니다."

DOCKER_VERSION=$(docker --version | awk '{print $3}' | tr -d ',')
info "Docker: $DOCKER_VERSION ✓"

# ── .env 설정 ────────────────────────────────────────────────────────────
header ".env 파일 설정"

if [ ! -f services/backend/.env ]; then
    cp services/backend/.env.example services/backend/.env
    warn ".env 파일을 services/backend/.env.example 에서 복사했습니다."
    warn "services/backend/.env 를 열어 GROQ_API_KEY, JWT_SECRET 등을 설정하세요."
else
    info "services/backend/.env 이미 존재 ✓"
fi

# ── credentials 디렉토리 ─────────────────────────────────────────────────
header "credentials 디렉토리"

mkdir -p credentials
if [ ! -f credentials/google-tts.json ]; then
    echo '{"type": "service_account", "note": "Replace with real Google credentials"}' \
        > credentials/google-tts.json
    warn "credentials/google-tts.json 더미 파일 생성됨 — 실제 파일로 교체 필요"
else
    info "credentials/google-tts.json 존재 ✓"
fi

# ── Docker Compose 기동 ──────────────────────────────────────────────────
header "인프라 기동 (MongoDB RS + Redis)"

info "이미지 풀링..."
docker compose -f docker/compose.backend.yml pull --quiet mongo-primary mongo-secondary redis nginx 2>/dev/null || true

info "MongoDB Primary + Redis 먼저 기동..."
docker compose -f docker/compose.backend.yml up -d mongo-primary redis

info "MongoDB Primary 헬스체크 대기 (최대 60초)..."
for i in $(seq 1 12); do
    if docker compose -f docker/compose.backend.yml exec -T mongo-primary mongosh --quiet --eval "db.adminCommand('ping').ok" >/dev/null 2>&1; then
        info "MongoDB Primary 준비됨 ✓"
        break
    fi
    if [ $i -eq 12 ]; then error "MongoDB Primary 헬스체크 타임아웃"; fi
    echo -n "."
    sleep 5
done

info "MongoDB Secondary 기동..."
docker compose -f docker/compose.backend.yml up -d mongo-secondary

info "Replica Set 초기화 (mongo-init)..."
docker compose -f docker/compose.backend.yml up mongo-init

# ── 백엔드 빌드 & 기동 ───────────────────────────────────────────────────
header "백엔드 빌드 및 기동"

info "FastAPI 이미지 빌드..."
docker compose -f docker/compose.backend.yml build api

info "전체 스택 기동..."
docker compose -f docker/compose.backend.yml up -d api nginx

# ── 헬스체크 ────────────────────────────────────────────────────────────
header "헬스체크"

info "API 응답 대기 (최대 30초)..."
for i in $(seq 1 6); do
    if curl -sf http://localhost/api/v1/health >/dev/null 2>&1; then
        info "API 응답 확인 ✓"
        break
    fi
    if [ $i -eq 6 ]; then
        warn "API 헬스체크 실패 — 로그를 확인하세요: docker compose logs api"
    fi
    sleep 5
done

# ── 결과 출력 ────────────────────────────────────────────────────────────
header "설정 완료"

echo ""
echo -e "${GREEN}AgentOE 로컬 환경이 준비되었습니다!${NC}"
echo ""
echo "  API          : http://localhost/api/v1"
echo "  Swagger UI   : http://localhost:8000/api/docs"
echo "  MongoDB      : mongodb://admin:***@localhost:27017/?replicaSet=rs0"
echo "  Redis        : redis://localhost:6379"
echo "  Mongo Express: http://localhost:8081 (dev mode 전용)"
echo ""
echo "유용한 명령어:"
echo "  로그 확인    : docker compose logs -f api"
echo "  전체 중단    : docker compose down"
echo "  데이터 초기화: docker compose down -v"
echo ""
