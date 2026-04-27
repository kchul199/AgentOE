#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# dev-integration.sh — 한 명령으로 통합 테스트 환경 기동 + smoke
#
# 명령:
#   ./dev-integration.sh up      네트워크 + 양쪽 stack + smoke 까지 전부
#   ./dev-integration.sh down    양쪽 stack 모두 정리 (volume 보존)
#   ./dev-integration.sh smoke   이미 떠 있는 backend 에 smoke 만
#   ./dev-integration.sh logs    bridge + agentoe-api 로그 tail
#   ./dev-integration.sh status  컨테이너 상태 한눈
#
# 환경변수:
#   AGENTOE_DIR  (기본 ~/AgenticOE_v2)
#   VBGW_DIR     (기본 ~/vbgw_v2)
#   NETWORK_NAME (기본 agentoe-vbgw-bridge)
#   SKIP_VBGW=1  → backend 만 띄우고 smoke (bridge 없이 wire 검증)
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

AGENTOE_DIR="${AGENTOE_DIR:-$HOME/AgenticOE_v2}"
VBGW_DIR="${VBGW_DIR:-$HOME/vbgw_v2}"
NETWORK_NAME="${NETWORK_NAME:-agentoe-vbgw-bridge}"
SKIP_VBGW="${SKIP_VBGW:-0}"

AGENTOE_SKEL="$AGENTOE_DIR/skeleton"
VBGW_FS="$VBGW_DIR/vbgw-freeswitch"

# 색상
RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${CYAN}[dev-integration]${NC} $*"; }
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
warn() { echo -e "${YEL}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; }

# ── 사전 검증 ────────────────────────────────────────────────────────
preflight() {
  command -v docker >/dev/null || { err "docker 미설치"; exit 1; }
  docker compose version >/dev/null 2>&1 || { err "docker compose v2 필요"; exit 1; }
  [[ -d "$AGENTOE_SKEL" ]] || { err "AGENTOE_DIR 잘못됨: $AGENTOE_SKEL"; exit 1; }
  if [[ "$SKIP_VBGW" != "1" ]]; then
    [[ -d "$VBGW_FS" ]] || { err "VBGW_DIR 잘못됨: $VBGW_FS"; exit 1; }
    [[ -f "$VBGW_FS/.env" ]] || warn "$VBGW_FS/.env 없음 — vbgw 가 ESL_PASSWORD 등 누락으로 실패 가능"
  fi
  command -v python3 >/dev/null || { err "python3 미설치 (smoke client 용)"; exit 1; }
  python3 -c "import grpc, grpc_health" 2>/dev/null \
    || { warn "grpcio + grpcio-health-checking 미설치. pip install --break-system-packages grpcio grpcio-health-checking"; }
}

ensure_network() {
  if docker network inspect "$NETWORK_NAME" >/dev/null 2>&1; then
    log "network '$NETWORK_NAME' 이미 존재"
  else
    log "network '$NETWORK_NAME' 생성"
    docker network create "$NETWORK_NAME" >/dev/null
  fi
}

up_backend() {
  log "AgentOE backend stack 기동 (mongo + redis + api + nginx)"
  ( cd "$AGENTOE_SKEL" && \
    docker compose -f docker-compose.yml \
                   -f docker-compose.dev.yml \
                   -f docker-compose.integration.yml up -d --remove-orphans )

  log "agentoe-api 의 HTTP livez + gRPC port 대기 (max 90s)"
  for i in $(seq 1 30); do
    HTTP_OK=$(curl -sf http://localhost:8000/api/v1/livez >/dev/null 2>&1 && echo y || echo n)
    GRPC_OK=$(timeout 1 bash -c '</dev/tcp/localhost/50051' 2>/dev/null && echo y || echo n)
    if [[ "$HTTP_OK" == "y" && "$GRPC_OK" == "y" ]]; then
      ok "agentoe-api 준비 완료 (HTTP+gRPC)"
      return 0
    fi
    sleep 3
  done
  err "agentoe-api 가 90s 안에 ready 안 됨. 로그:"
  docker compose -f "$AGENTOE_SKEL/docker-compose.yml" logs --tail 50 api || true
  exit 1
}

up_vbgw() {
  log "vbgw stack 기동 (freeswitch + bridge + orchestrator)"
  ( cd "$VBGW_FS" && \
    docker compose -f docker-compose.yml \
                   -f docker-compose.integration.yml up -d --remove-orphans )

  log "vbgw-bridge 의 internal health 대기 (max 60s)"
  for i in $(seq 1 20); do
    if docker exec vbgw-bridge wget -q -O- http://127.0.0.1:8091/internal/health >/dev/null 2>&1; then
      ok "vbgw-bridge 준비 완료"
      log "  → bridge env AI_GRPC_ADDR:"
      docker exec vbgw-bridge sh -c 'echo $AI_GRPC_ADDR'
      return 0
    fi
    sleep 3
  done
  err "vbgw-bridge ready timeout"
  docker logs --tail 50 vbgw-bridge || true
  exit 1
}

smoke() {
  log "smoke gRPC client 실행 (bridge 우회 — backend 직접 검증)"
  python3 "$AGENTOE_SKEL/scripts/integration/smoke_grpc_client.py" \
    --addr localhost:50051 --calls 3 --tenant smoke \
    || { err "smoke 실패 — 위 출력 참고"; return 1; }

  if [[ "$SKIP_VBGW" != "1" ]]; then
    log "bridge → backend wire 검증 (bridge 컨테이너 안에서 grpc-health-probe)"
    if docker exec vbgw-bridge sh -c '
        which grpc-health-probe 2>/dev/null \
          || { echo "grpc-health-probe 미설치 — 컨테이너 안에서 nc 로 fallback"; \
               nc -z agentoe-api 50051 && echo "TCP reachable"; }' ; then
      ok "bridge → agentoe-api gRPC 도달 가능"
    else
      err "bridge 가 agentoe-api 에 도달 못 함 — network join 검증 필요"
      docker inspect vbgw-bridge --format='{{json .NetworkSettings.Networks}}' | python3 -m json.tool
      return 1
    fi
  fi
  ok "통합 smoke 모두 통과 — staging cutover 진행 가능"
}

down() {
  if [[ "$SKIP_VBGW" != "1" ]]; then
    log "vbgw stack down"
    ( cd "$VBGW_FS" && \
      docker compose -f docker-compose.yml \
                     -f docker-compose.integration.yml down --remove-orphans ) || true
  fi
  log "AgentOE backend stack down"
  ( cd "$AGENTOE_SKEL" && \
    docker compose -f docker-compose.yml \
                   -f docker-compose.dev.yml \
                   -f docker-compose.integration.yml down --remove-orphans ) || true
  log "network 는 보존 (수동 삭제: docker network rm $NETWORK_NAME)"
}

logs() {
  log "agentoe-api + vbgw-bridge 로그 (Ctrl+C 로 종료)"
  docker logs -f --tail=20 agentoe-api &
  PID1=$!
  if [[ "$SKIP_VBGW" != "1" ]]; then
    docker logs -f --tail=20 vbgw-bridge &
    PID2=$!
  fi
  trap 'kill $PID1 ${PID2:-} 2>/dev/null || true' INT TERM
  wait
}

status() {
  log "통합 환경 컨테이너:"
  docker ps --filter 'name=agentoe-' --filter 'name=vbgw-' \
    --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}'
  echo
  log "공유 network ($NETWORK_NAME) 가입 현황:"
  docker network inspect "$NETWORK_NAME" \
    --format '{{range $k,$v := .Containers}}{{$v.Name}} ({{$v.IPv4Address}}){{"\n"}}{{end}}' 2>/dev/null \
    || warn "network 미존재"
}

# ── main ────────────────────────────────────────────────────────────
case "${1:-}" in
  up)
    preflight
    ensure_network
    up_backend
    [[ "$SKIP_VBGW" != "1" ]] && up_vbgw
    smoke
    status
    ;;
  smoke)  preflight; smoke ;;
  down)   down ;;
  logs)   logs ;;
  status) status ;;
  *)
    cat <<EOF
Usage: $0 {up|down|smoke|logs|status}

  up       전체 환경 기동 + smoke 실행
  smoke    이미 떠 있는 backend 에 smoke 만 (gRPC 직접)
  down     양쪽 stack 정리 (volume 보존)
  logs     agentoe-api + vbgw-bridge 로그 tail
  status   컨테이너 / 네트워크 상태

환경변수:
  AGENTOE_DIR  ($HOME/AgenticOE_v2)
  VBGW_DIR     ($HOME/vbgw_v2)
  SKIP_VBGW=1  vbgw 안 띄우고 backend gRPC 만 wire 검증
EOF
    exit 1 ;;
esac
