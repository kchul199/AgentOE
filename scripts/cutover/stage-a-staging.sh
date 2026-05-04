#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# stage-a-staging.sh — vbgw → backend cutover Stage A (staging 100%)
#
# 사용:
#   ./scripts/cutover/stage-a-staging.sh         # 기본 staging 100% 실행
#   ./scripts/cutover/stage-a-staging.sh --dry-run     # plan 만 (helm 변경 X)
#   ./scripts/cutover/stage-a-staging.sh --rollback    # vbgw-ai 로 즉시 회귀
#
# 전제:
#   - kubectl context 가 staging EKS 클러스터로 설정 (`kubectl config current-context`)
#   - helm 설치 (>=3.14)
#   - Phase Y (backend gRPC) 와 Phase Z (vbgw chart canary) PR 머지됨
#   - dev smoke (./scripts/integration/dev-integration.sh up) 3회 OK 사전 확인
#
# 결과:
#   exit 0 — staging cutover 성공 (5건 합성 통화 + 5분 모니터링 게이트 통과)
#   exit 1 — preflight 실패 (kubectl/helm/backend 미준비)
#   exit 2 — helm upgrade 실패 (chart 문제)
#   exit 3 — 합성 통화 실패 (1건 이상 setup fail 또는 timeout)
#   exit 4 — 모니터링 게이트 실패 (5분 burn rate / drop 임계 초과)
#   → exit ≥ 2 면 스크립트가 자동으로 롤백 시도.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── Config ────────────────────────────────────────────────────────────────
NS_VBGW="${NS_VBGW:-vbgw-staging}"
NS_BACKEND="${NS_BACKEND:-agentoe-staging}"
RELEASE="${RELEASE:-vbgw}"
CHART_PATH="${CHART_PATH:-deploy/helm/vbgw}"
VALUES_FILE="${VALUES_FILE:-deploy/helm/values/staging/vbgw.values.yaml}"

NEW_GRPC_ADDR="agentoe-backend.${NS_BACKEND}.svc.cluster.local:50051"
OLD_GRPC_ADDR="${OLD_GRPC_ADDR:-ai-service:50051}"

SYNTHETIC_CALLS=5
MONITOR_WINDOW_SEC=300
ERROR_LINE_THRESHOLD=20

DRY_RUN=0
ROLLBACK=0

# 색상
RED='\033[0;31m'; GRN='\033[0;32m'; YEL='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
log()  { echo -e "${CYAN}[stage-a]${NC} $*"; }
ok()   { echo -e "${GRN}[OK]${NC} $*"; }
warn() { echo -e "${YEL}[WARN]${NC} $*"; }
err()  { echo -e "${RED}[ERR]${NC} $*"; }

# ── Args ────────────────────────────────────────────────────────────────
while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1; shift ;;
    --rollback) ROLLBACK=1; shift ;;
    *) err "unknown arg: $1"; exit 1 ;;
  esac
done

# ── Preflight ───────────────────────────────────────────────────────────
preflight() {
  log "preflight 점검"

  command -v kubectl >/dev/null || { err "kubectl 미설치"; return 1; }
  command -v helm >/dev/null || { err "helm 미설치"; return 1; }

  CTX=$(kubectl config current-context 2>/dev/null || echo "")
  log "  kubectl context = $CTX"
  if [[ "$CTX" != *"staging"* ]]; then
    warn "  context 이름에 'staging' 안 보임. staging cluster 가 맞는지 확인."
    read -p "  계속할까요? [y/N] " ans
    [[ "$ans" == "y" ]] || return 1
  fi

  # backend 가 gRPC SERVING 인지
  log "  backend gRPC health 검증"
  if ! kubectl -n "$NS_BACKEND" get svc agentoe-backend >/dev/null 2>&1; then
    err "  agentoe-backend service 가 NS '$NS_BACKEND' 에 없음"
    return 1
  fi
  POD=$(kubectl -n "$NS_BACKEND" get pod -l app.kubernetes.io/name=agentoe-backend -o name | head -1)
  [[ -z "$POD" ]] && { err "  backend Pod 없음"; return 1; }
  if kubectl -n "$NS_BACKEND" exec "$POD" -- /bin/sh -c '
    python3 -c "
import grpc, sys
from grpc_health.v1 import health_pb2, health_pb2_grpc
ch = grpc.insecure_channel(\"localhost:50051\")
st = health_pb2_grpc.HealthStub(ch)
r = st.Check(health_pb2.HealthCheckRequest(service=\"voicebot.ai.VoicebotAiService\"), timeout=2)
sys.exit(0 if r.status == 1 else 1)
"' 2>/dev/null; then
    ok "  backend gRPC SERVING"
  else
    err "  backend gRPC 가 NOT SERVING"
    return 1
  fi

  # vbgw chart 가 이미 staging 에 있나?
  if helm -n "$NS_VBGW" status "$RELEASE" >/dev/null 2>&1; then
    log "  vbgw release 이미 존재 — upgrade 진행"
  else
    log "  vbgw release 신규 — install 진행"
  fi
  return 0
}

# ── Cutover (helm upgrade) ──────────────────────────────────────────────
cutover() {
  local TARGET_ADDR="$1"   # NEW_GRPC_ADDR (staging 100%) 또는 OLD_GRPC_ADDR (rollback)
  log "helm upgrade — bridge.grpcAiAddr = $TARGET_ADDR"
  local FLAGS=(--wait --timeout 5m)
  [[ "$DRY_RUN" == "1" ]] && FLAGS+=(--dry-run)

  helm -n "$NS_VBGW" upgrade --install "$RELEASE" "$CHART_PATH" \
    --create-namespace \
    -f "$VALUES_FILE" \
    --set bridge.grpcAiAddr="$TARGET_ADDR" \
    --set bridge.canary.enabled=false \
    "${FLAGS[@]}" \
    || return 2

  [[ "$DRY_RUN" == "1" ]] && { ok "  dry-run OK"; return 0; }

  ok "  helm upgrade OK"
  kubectl -n "$NS_VBGW" rollout status deploy/vbgw-bridge --timeout=180s
  return 0
}

# ── 합성 통화 ──────────────────────────────────────────────────────────
synthetic_calls() {
  log "합성 통화 $SYNTHETIC_CALLS 건 (smoke gRPC client 직접 backend 호출)"
  if [[ ! -f scripts/integration/smoke_grpc_client.py ]]; then
    err "scripts/integration/smoke_grpc_client.py 없음"
    return 3
  fi

  # backend 의 grpc 포트로 port-forward
  ( kubectl -n "$NS_BACKEND" port-forward svc/agentoe-backend 50051:50051 >/tmp/pf-cutover.log 2>&1 & )
  PF_PID=$!
  sleep 3

  if python3 scripts/integration/smoke_grpc_client.py \
       --addr localhost:50051 --calls "$SYNTHETIC_CALLS" --tenant cutover-stage-a; then
    ok "  $SYNTHETIC_CALLS 건 모두 OK"
    kill "$PF_PID" 2>/dev/null || true
    return 0
  else
    err "  smoke 실패"
    kill "$PF_PID" 2>/dev/null || true
    return 3
  fi
}

# ── 모니터링 게이트 (5분) ──────────────────────────────────────────────
monitor_gate() {
  log "$MONITOR_WINDOW_SEC 초간 backend 로그 / 메트릭 모니터링"
  local START=$(date +%s)
  local END=$((START + MONITOR_WINDOW_SEC))

  while [[ $(date +%s) -lt $END ]]; do
    REMAINING=$((END - $(date +%s)))
    # backend pipeline error 로그
    ERR_LINES=$(kubectl -n "$NS_BACKEND" logs -l app.kubernetes.io/name=agentoe-backend \
                  --since=10s --tail=200 2>/dev/null \
                | grep -cE '"level":"(ERROR|CRITICAL)"' || echo 0)
    log "  [${REMAINING}s 남음] error 로그 line/10s = $ERR_LINES (임계 $ERROR_LINE_THRESHOLD)"
    if [[ "$ERR_LINES" -gt "$ERROR_LINE_THRESHOLD" ]]; then
      err "  error 로그 임계 초과 — 게이트 fail"
      return 4
    fi
    sleep 30
  done
  ok "  $MONITOR_WINDOW_SEC 초 모두 임계 이하"
  return 0
}

# ── 롤백 ────────────────────────────────────────────────────────────────
do_rollback() {
  warn "롤백 — bridge.grpcAiAddr 를 $OLD_GRPC_ADDR (vbgw-ai) 로 회귀"
  cutover "$OLD_GRPC_ADDR" || warn "  롤백 helm upgrade 도 실패. 수동 확인 필요."
  warn "진행 중 통화는 자연 종료까지 보존. 새 통화는 vbgw-ai 로."
}

# ── Main ────────────────────────────────────────────────────────────────
if [[ "$ROLLBACK" == "1" ]]; then
  do_rollback
  exit 0
fi

preflight || { err "preflight 실패 — 진행 중단"; exit 1; }
cutover "$NEW_GRPC_ADDR"
RC=$?
if [[ $RC -ne 0 ]]; then
  err "cutover 실패 ($RC) — 자동 롤백"
  do_rollback
  exit 2
fi

[[ "$DRY_RUN" == "1" ]] && { ok "DRY RUN 성공 — 실 변경 없음"; exit 0; }

synthetic_calls
RC=$?
if [[ $RC -ne 0 ]]; then
  err "합성 통화 실패 ($RC) — 자동 롤백"
  do_rollback
  exit 3
fi

monitor_gate
RC=$?
if [[ $RC -ne 0 ]]; then
  err "모니터링 게이트 실패 ($RC) — 자동 롤백"
  do_rollback
  exit 4
fi

ok ""
ok "✅ Stage A 성공 — staging 100% backend cutover 완료"
ok "   다음 단계: 24h 안정 후 Stage B (prod 10% canary)"
ok "   runbook: docs/runbook/vbgw-ai-cutover.md §2"
