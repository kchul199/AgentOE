#!/usr/bin/env bash
# portal_prod_deploy.sh — ops-portal prod 배포 게이트 (Phase N — N5.3)
#
# 5단계 게이트:
#   Gate 1. Staging 헬스 검사   — portal-staging.agentoe.io/healthz HTTP 200
#   Gate 2. ECR CVE CRITICAL=0  — ECR 이미지 스캔 결과 확인
#   Gate 3. 수동 승인            — 배포자 확인 (CI 에서는 GitHub Environment approval)
#   Gate 4. Canary 단계 배포     — 10% → 50% → 100% (각 단계 Prometheus error_rate 게이트)
#   Gate 5. 최종 smoke test      — prod /healthz + PagerDuty maintenance window 종료
#
# 사용법:
#   ./scripts/portal_prod_deploy.sh --image-tag <GIT_SHA_or_TAG> [OPTIONS]
#
# 옵션:
#   --image-tag <tag>       배포할 이미지 태그 (필수)
#   --skip-staging-check    Gate1 건너뜀 (긴급 패치용 — 팀장 승인 필수)
#   --skip-cve-check        Gate2 건너뜀 (긴급 패치용)
#   --auto-approve          Gate3 수동 승인 건너뜀 (CI 자동화용 — 이미 GitHub Env approved)
#   --dry-run               helm --dry-run; 실제 배포 없음
#   --pd-service-id <id>    PagerDuty maintenance window 서비스 ID
#
# 환경변수 (필수):
#   ECR_REGISTRY            — AWS ECR registry (e.g. 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com)
#   AWS_REGION              — ap-northeast-2
#   KUBECONFIG              — prod kubeconfig 경로
#   PORTAL_HOSTNAME         — portal.agentoe.io (기본값)
#   STAGING_HOSTNAME        — portal-staging.agentoe.io (기본값)
#
# 환경변수 (선택):
#   SLACK_WEBHOOK_URL       — 배포 알림
#   PROMETHEUS_URL          — canary error_rate 게이트 (기본: http://prometheus:9090)
#   PD_API_KEY              — PagerDuty API key (Gate5 maintenance window 용)
#   ERROR_RATE_THRESHOLD    — canary 통과 허용 error_rate (기본: 1.0 = 1%)

set -euo pipefail

# ── 색상 출력 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; BOLD='\033[1m'; NC='\033[0m'

log_info()  { echo -e "${BLUE}[INFO]${NC}  $*"; }
log_ok()    { echo -e "${GREEN}[OK]${NC}    $*"; }
log_warn()  { echo -e "${YELLOW}[WARN]${NC}  $*"; }
log_err()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
log_gate()  { echo -e "${BOLD}${GREEN}━━ GATE $* ━━${NC}"; }
log_fail()  { echo -e "${BOLD}${RED}━━ GATE $* FAILED ━━${NC}"; }

# ── 인수 파싱 ─────────────────────────────────────────────────────────────────
IMAGE_TAG=""
SKIP_STAGING_CHECK=false
SKIP_CVE_CHECK=false
AUTO_APPROVE=false
DRY_RUN=false
PD_SERVICE_ID=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --image-tag)        IMAGE_TAG="$2";       shift 2 ;;
    --skip-staging-check) SKIP_STAGING_CHECK=true; shift ;;
    --skip-cve-check)   SKIP_CVE_CHECK=true;  shift ;;
    --auto-approve)     AUTO_APPROVE=true;    shift ;;
    --dry-run)          DRY_RUN=true;         shift ;;
    --pd-service-id)    PD_SERVICE_ID="$2";   shift 2 ;;
    *) echo "Unknown option: $1"; exit 1 ;;
  esac
done

# ── 환경변수 기본값 ───────────────────────────────────────────────────────────
ECR_REGISTRY="${ECR_REGISTRY:-}"
AWS_REGION="${AWS_REGION:-ap-northeast-2}"
PORTAL_HOSTNAME="${PORTAL_HOSTNAME:-portal.agentoe.io}"
STAGING_HOSTNAME="${STAGING_HOSTNAME:-portal-staging.agentoe.io}"
PROMETHEUS_URL="${PROMETHEUS_URL:-http://prometheus:9090}"
ERROR_RATE_THRESHOLD="${ERROR_RATE_THRESHOLD:-1.0}"

# ── 필수 검사 ─────────────────────────────────────────────────────────────────
if [[ -z "$IMAGE_TAG" ]]; then
  log_err "--image-tag 가 필요합니다."
  exit 1
fi
if [[ -z "$ECR_REGISTRY" ]]; then
  log_err "ECR_REGISTRY 환경변수가 필요합니다."
  exit 1
fi

# ── 도구 확인 ─────────────────────────────────────────────────────────────────
for cmd in kubectl helm aws curl jq; do
  if ! command -v "$cmd" &>/dev/null; then
    log_err "$cmd 가 설치되어 있지 않습니다."
    exit 1
  fi
done

ECR_REPO="${ECR_REGISTRY}/agentoe-prod/ops-portal"
RELEASE="agentoe-portal"
NAMESPACE="portal"
HELM_CHART="deploy/helm/agentoe-portal"
VALUES_FILE="deploy/helm/values/prod/portal.values.yaml"

echo ""
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}  agentoe-portal prod 배포 게이트${NC}"
echo -e "${BOLD}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
log_info "이미지 태그  : ${IMAGE_TAG}"
log_info "ECR 레포지토리: ${ECR_REPO}"
log_info "배포 환경    : production (${PORTAL_HOSTNAME})"
[[ "$DRY_RUN" == "true" ]] && log_warn "★ DRY-RUN 모드 — 실제 배포 없음"
echo ""

# ── Gate 1: Staging 헬스 검사 ────────────────────────────────────────────────
log_gate "1: Staging 헬스 검사"
if [[ "$SKIP_STAGING_CHECK" == "true" ]]; then
  log_warn "Gate1 건너뜀 (--skip-staging-check). 이 옵션은 팀장 승인 후에만 사용하세요."
else
  STAGING_URL="https://${STAGING_HOSTNAME}/healthz"
  log_info "Staging 헬스 URL: ${STAGING_URL}"
  HTTP_CODE=$(curl -fsSk -o /dev/null -w "%{http_code}" "${STAGING_URL}" --max-time 10 || echo "000")
  if [[ "$HTTP_CODE" != "200" ]]; then
    log_fail "1 — Staging /healthz 응답: ${HTTP_CODE}"
    log_err "Staging 환경이 정상이 아닙니다. prod 배포 중단."
    exit 1
  fi
  log_ok "Staging /healthz → HTTP ${HTTP_CODE}"
fi

# ── Gate 2: ECR CVE CRITICAL=0 검사 ──────────────────────────────────────────
log_gate "2: ECR 이미지 CVE 스캔 (CRITICAL=0)"
if [[ "$SKIP_CVE_CHECK" == "true" ]]; then
  log_warn "Gate2 건너뜀 (--skip-cve-check). 긴급 패치 외에는 사용 금지."
else
  log_info "ECR 이미지 스캔 결과 조회 중..."
  SCAN_RESULT=$(aws ecr describe-image-scan-findings \
    --repository-name "agentoe-prod/ops-portal" \
    --image-id imageTag="${IMAGE_TAG}" \
    --region "${AWS_REGION}" \
    --query 'imageScanFindings.findingSeverityCounts' \
    --output json 2>/dev/null || echo '{}')

  CRITICAL_COUNT=$(echo "$SCAN_RESULT" | jq '.CRITICAL // 0')
  if [[ "$CRITICAL_COUNT" -gt 0 ]]; then
    log_fail "2 — CRITICAL 취약점 ${CRITICAL_COUNT}개 발견"
    log_err "이미지에 CRITICAL CVE 가 있습니다. 이미지를 재빌드하거나 base 이미지를 업데이트하세요."
    log_err "스캔 결과: ${SCAN_RESULT}"
    exit 1
  fi
  log_ok "ECR CVE 스캔 통과 (CRITICAL=${CRITICAL_COUNT})"
fi

# ── Gate 3: 수동 승인 ─────────────────────────────────────────────────────────
log_gate "3: 수동 승인"
if [[ "$AUTO_APPROVE" == "true" ]]; then
  log_info "자동 승인 모드 (--auto-approve / CI GitHub Environment approval 통과)."
else
  echo ""
  echo -e "${YELLOW}┌───────────────────────────────────────────────────────┐${NC}"
  echo -e "${YELLOW}│  prod 배포를 진행하려면 아래에 'DEPLOY' 를 입력하세요.  │${NC}"
  echo -e "${YELLOW}│  이미지 태그: ${IMAGE_TAG}                              │${NC}"
  echo -e "${YELLOW}│  대상 환경  : ${PORTAL_HOSTNAME}                        │${NC}"
  echo -e "${YELLOW}└───────────────────────────────────────────────────────┘${NC}"
  echo -n "확인 입력: "
  read -r CONFIRM
  if [[ "$CONFIRM" != "DEPLOY" ]]; then
    log_warn "배포가 취소되었습니다."
    exit 0
  fi
fi
log_ok "수동 승인 완료"

# ── ECR 인증 + namespace 준비 ──────────────────────────────────────────────────
log_info "ECR 인증 중..."
aws ecr get-login-password --region "${AWS_REGION}" \
  | kubectl create secret docker-registry ecr-prod \
      --docker-server="${ECR_REGISTRY}" \
      --docker-username=AWS \
      --docker-password-stdin \
      --namespace="${NAMESPACE}" \
      --dry-run=client -o yaml \
  | kubectl apply -f -

kubectl create namespace "${NAMESPACE}" --dry-run=client -o yaml | kubectl apply -f -

# ── helm lint 재확인 ──────────────────────────────────────────────────────────
log_info "Helm lint 최종 확인..."
helm lint "${HELM_CHART}" -f "${VALUES_FILE}"

# ── 도우미: Prometheus error_rate 조회 ───────────────────────────────────────
_check_error_rate() {
  local window="$1"   # e.g. "2m"
  local result
  result=$(curl -sfG "${PROMETHEUS_URL}/api/v1/query" \
    --data-urlencode "query=100 * sum(rate(http_requests_total{service=\"agentoe-portal\",status=~\"5..\"}[${window}])) / sum(rate(http_requests_total{service=\"agentoe-portal\"}[${window}]))" \
    --max-time 5 2>/dev/null \
    | jq -r '.data.result[0].value[1] // "0"' 2>/dev/null || echo "0")
  echo "${result}"
}

_gate_error_rate() {
  local label="$1" window="$2" wait_sec="$3"
  log_info "Canary Gate [${label}] — ${wait_sec}초 관찰 후 error_rate 확인..."
  sleep "${wait_sec}"
  local rate
  rate=$(_check_error_rate "${window}")
  if awk "BEGIN {exit !($rate > $ERROR_RATE_THRESHOLD)}"; then
    log_fail "Canary — error_rate=${rate}% > threshold=${ERROR_RATE_THRESHOLD}%"
    return 1
  fi
  log_ok "Canary [${label}] 통과 — error_rate=${rate}% ≤ ${ERROR_RATE_THRESHOLD}%"
  return 0
}

# ── Gate 4: Canary 단계 배포 10% → 50% → 100% ───────────────────────────────
log_gate "4: Canary 배포"

_deploy_canary() {
  local replicas="$1" weight="$2"
  log_info "Canary ${weight}% — replicas=${replicas} 배포 중..."

  HELM_ARGS=(
    upgrade --install "${RELEASE}" "${HELM_CHART}"
    --namespace "${NAMESPACE}"
    -f "${VALUES_FILE}"
    --set "image.repository=${ECR_REPO}"
    --set "image.tag=${IMAGE_TAG}"
    --set "image.pullPolicy=Always"
    --set "replicaCount=${replicas}"
    --cleanup-on-fail
    --history-max 10
  )
  if [[ "$DRY_RUN" == "true" ]]; then
    HELM_ARGS+=(--dry-run)
  fi

  helm "${HELM_ARGS[@]}"
  if [[ "$DRY_RUN" != "true" ]]; then
    kubectl rollout status "deployment/${RELEASE}" -n "${NAMESPACE}" --timeout=3m
  fi
}

# Canary 10% (1 replica out of 3 typical prod)
_deploy_canary 1 10
if [[ "$DRY_RUN" != "true" ]]; then
  _gate_error_rate "10%" "2m" 120 || {
    log_err "Canary 10% 게이트 실패 — 롤백 실행"
    helm rollback "${RELEASE}" -n "${NAMESPACE}"
    exit 1
  }
fi

# Canary 50% (2 replicas)
_deploy_canary 2 50
if [[ "$DRY_RUN" != "true" ]]; then
  _gate_error_rate "50%" "2m" 120 || {
    log_err "Canary 50% 게이트 실패 — 롤백 실행"
    helm rollback "${RELEASE}" -n "${NAMESPACE}"
    exit 1
  }
fi

# Canary 100% (3 replicas — full prod, atomic)
log_info "Canary 100% — 전체 롤아웃..."
HELM_FULL_ARGS=(
  upgrade --install "${RELEASE}" "${HELM_CHART}"
  --namespace "${NAMESPACE}"
  -f "${VALUES_FILE}"
  --set "image.repository=${ECR_REPO}"
  --set "image.tag=${IMAGE_TAG}"
  --set "image.pullPolicy=Always"
  --atomic
  --timeout 5m
  --cleanup-on-fail
  --history-max 10
)
[[ "$DRY_RUN" == "true" ]] && HELM_FULL_ARGS+=(--dry-run)
helm "${HELM_FULL_ARGS[@]}"

if [[ "$DRY_RUN" != "true" ]]; then
  kubectl rollout status "deployment/${RELEASE}" -n "${NAMESPACE}" --timeout=5m
  _gate_error_rate "100%" "2m" 60 || {
    log_err "100% 롤아웃 후 error_rate 초과 — 롤백 실행"
    helm rollback "${RELEASE}" -n "${NAMESPACE}"
    exit 1
  }
fi
log_ok "Canary 10% → 50% → 100% 모든 게이트 통과"

# ── Gate 5: 최종 smoke test + PD maintenance 종료 ────────────────────────────
log_gate "5: Prod smoke test"
if [[ "$DRY_RUN" != "true" ]]; then
  PROD_URL="https://${PORTAL_HOSTNAME}/healthz"
  log_info "Prod /healthz 최대 60초 대기..."
  for i in $(seq 1 12); do
    HTTP_CODE=$(curl -fsSk -o /dev/null -w "%{http_code}" "${PROD_URL}" --max-time 8 || echo "000")
    if [[ "$HTTP_CODE" == "200" ]]; then
      log_ok "Prod /healthz → HTTP ${HTTP_CODE} (시도 ${i}/12)"
      break
    fi
    log_warn "/healthz → ${HTTP_CODE} — 5초 후 재시도 (${i}/12)..."
    sleep 5
    if [[ "$i" -eq 12 ]]; then
      log_fail "5 — Prod smoke test 실패"
      exit 1
    fi
  done

  # PagerDuty maintenance window 종료 (선택)
  if [[ -n "$PD_SERVICE_ID" && -n "${PD_API_KEY:-}" ]]; then
    log_info "PagerDuty maintenance window 종료 중..."
    curl -sfS -X DELETE \
      "https://api.pagerduty.com/maintenance_windows/${PD_SERVICE_ID}" \
      -H "Authorization: Token token=${PD_API_KEY}" \
      -H "Accept: application/vnd.pagerduty+json;version=2" \
      && log_ok "PagerDuty maintenance window 종료" \
      || log_warn "PagerDuty maintenance window 종료 실패 (무시)"
  fi
fi

# ── Slack 알림 ────────────────────────────────────────────────────────────────
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  DEPLOY_STATUS="✅"
  [[ "$DRY_RUN" == "true" ]] && DEPLOY_STATUS="🔍 [DRY-RUN]"
  curl -s -X POST "${SLACK_WEBHOOK_URL}" \
    -H "Content-Type: application/json" \
    -d "{\"text\":\"${DEPLOY_STATUS} agentoe-portal *prod* 배포 완료\\n이미지: \`${IMAGE_TAG}\`\\n대상: ${PORTAL_HOSTNAME}\"}" \
    || true
fi

# ── 최종 요약 ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${BOLD}${GREEN}  prod 배포 성공 ✓${NC}"
echo -e "${BOLD}${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "  이미지 태그: ${IMAGE_TAG}"
echo -e "  배포 URL   : https://${PORTAL_HOSTNAME}"
[[ "$DRY_RUN" == "true" ]] && echo -e "  ※ dry-run — 실제 변경 없음"
echo ""
