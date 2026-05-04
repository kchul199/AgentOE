#!/usr/bin/env bash
# 전체 스택 헬스체크
set -euo pipefail

GREEN='\033[0;32m'; RED='\033[0;31m'; NC='\033[0m'
ok()   { echo -e "${GREEN}✓${NC} $1"; }
fail() { echo -e "${RED}✗${NC} $1"; FAILED=1; }

FAILED=0

echo "=== AgentOE 스택 헬스체크 ==="

# API
if curl -sf http://localhost/api/v1/health | grep -q '"status":"ok"'; then
    ok "API (http://localhost/api/v1/health)"
else
    fail "API 응답 없음"
fi

# MongoDB
if docker compose exec -T mongo-primary mongosh --quiet \
    --eval "db.adminCommand('ping').ok" 2>/dev/null | grep -q 1; then
    ok "MongoDB Primary"
else
    fail "MongoDB Primary 응답 없음"
fi

# MongoDB Replica Set
RS_STATE=$(docker compose exec -T mongo-primary mongosh --quiet \
    --eval "rs.status().myState" 2>/dev/null || echo "0")
if [ "$RS_STATE" = "1" ]; then
    ok "MongoDB Replica Set (PRIMARY)"
else
    fail "MongoDB Replica Set 상태 이상 (state=$RS_STATE)"
fi

# Redis
if docker compose exec -T redis redis-cli ping 2>/dev/null | grep -q PONG; then
    ok "Redis"
else
    fail "Redis 응답 없음"
fi

echo ""
if [ "$FAILED" = "0" ]; then
    echo -e "${GREEN}모든 서비스 정상${NC}"
else
    echo -e "${RED}일부 서비스 이상 — docker compose logs 로 확인하세요${NC}"
    exit 1
fi
