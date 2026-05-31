#!/usr/bin/env bash
# portal_staging_deploy.sh — agentoe-portal staging 배포 스크립트 (Phase N — N4.3)
#
# 사용법:
#   ./scripts/portal_staging_deploy.sh [--image-tag <tag>] [--dry-run] [--skip-smoke]
#
# 필수 환경변수 (미설정 시 .env.staging 에서 로드 시도):
#   ECR_REGISTRY      예) 123456789012.dkr.ecr.ap-northeast-2.amazonaws.com
#   AWS_REGION        예) ap-northeast-2
#   KUBECONFIG        EKS staging 클러스터 kubeconfig 경로
#   PORTAL_HOSTNAME   예) portal-staging.agentoe.io  (smoke test 대상)
#
# 선택 환경변수:
#   HELM_RELEASE      기본: agentoe-portal
#   HELM_NAMESPACE    기본: portal
#   HELM_CHART        기본: deploy/helm/agentoe-portal
#   HELM_VALUES       기본: deploy/helm/values/staging/portal.values.yaml
#   IMAGE_TAG         기본: git short SHA (--image-tag 로 override 가능)
#   SMOKE_RETRIES     기본: 12  (× 5s = 최대 60초 대기)
#   SLACK_WEBHOOK_URL 설정 시 배포 결과를 Slack 으로 전송

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

# ── 색깔 출력 ─────────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
info()    { echo -e "${BLUE}[INFO]${NC}  $*"; }
success() { echo -e "${GREEN}[OK]${NC}    $*"; }
warn()    { echo -e "${YELLOW}[WARN]${NC}  $*"; }
error()   { echo -e "${RED}[ERROR]${NC} $*" >&2; }
die()     { error "$*"; exit 1; }

# ── CLI 파싱 ──────────────────────────────────────────────────────────────────
DRY_RUN=false
SKIP_SMOKE=false
CLI_IMAGE_TAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --image-tag)  CLI_IMAGE_TAG="$2"; shift 2 ;;
    --dry-run)    DRY_RUN=true; shift ;;
    --skip-smoke) SKIP_SMOKE=true; shift ;;
    -h|--help)
      sed -n '2,20p' "$0" | sed 's/^# //'
      exit 0 ;;
    *) die "알 수 없는 옵션: $1" ;;
  esac
done

# ── 환경변수 로드 ──────────────────────────────────────────────────────────────
if [[ -f "${REPO_ROOT}/.env.staging" ]]; then
  # shellcheck disable=SC1090
  source "${REPO_ROOT}/.env.staging"
  info ".env.staging 로드됨"
fi

ECR_REGISTRY="${ECR_REGISTRY:-}"
AWS_REGION="${AWS_REGION:-ap-northeast-2}"
PORTAL_HOSTNAME="${PORTAL_HOSTNAME:-portal-staging.agentoe.io}"
HELM_RELEASE="${HELM_RELEASE:-agentoe-portal}"
HELM_NAMESPACE="${HELM_NAMESPACE:-portal}"
HELM_CHART="${HELM_CHART:-${REPO_ROOT}/deploy/helm/agentoe-portal}"
HELM_VALUES="${HELM_VALUES:-${REPO_ROOT}/deploy/helm/values/staging/portal.values.yaml}"
SMOKE_RETRIES="${SMOKE_RETRIES:-12}"

# IMAGE_TAG: CLI > 환경변수 > git SHA
if [[ -n "$CLI_IMAGE_TAG" ]]; then
  IMAGE_TAG="$CLI_IMAGE_TAG"
elif [[ -n "${IMAGE_TAG:-}" ]]; then
  IMAGE_TAG="${IMAGE_TAG}"
else
  IMAGE_TAG="$(git -C "${REPO_ROOT}" rev-parse --short=12 HEAD)"
fi

# ── banner ──────────────────────────────────────────────────────────────────
echo -e "\n${BLUE}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║   agentoe-portal staging 배포                        ║${NC}"
echo -e "${BLUE}╚══════════════════════════════════════════════════════╝${NC}"
info "image tag  : ${IMAGE_TAG}"
info "helm chart : ${HELM_CHART}"
info "values     : ${HELM_VALUES}"
info "namespace  : ${HELM_NAMESPACE}"
info "dry-run    : ${DRY_RUN}"
echo ""

# ── 1. Preflight 검사 ─────────────────────────────────────────────────────────
info "=== [1/6] Preflight 검사 ==="

# 필수 도구
for cmd in kubectl helm curl aws; do
  command -v "$cmd" &>/dev/null || die "'$cmd' 이(가) PATH 에 없습니다. 설치 후 재시도하세요."
done
success "필수 도구 확인 완료 (kubectl / helm / curl / aws)"

# KUBECONFIG 확인
[[ -n "${KUBECONFIG:-}" ]] || die "KUBECONFIG 환경변수가 설정되지 않았습니다."
kubectl cluster-info --request-timeout=10s &>/dev/null || die "kubectl cluster-info 실패 — kubeconfig 또는 클러스터 상태 확인"
CLUSTER="$(kubectl config current-context)"
success "클러스터 연결 확인: ${CLUSTER}"

# ECR 레지스트리 확인
[[ -n "$ECR_REGISTRY" ]] || die "ECR_REGISTRY 환경변수가 설정되지 않았습니다."

# Helm chart 존재 확인
[[ -d "$HELM_CHART" ]] || die "Helm chart 디렉토리가 없습니다: $HELM_CHART"
[[ -f "$HELM_VALUES" ]] || die "values 파일이 없습니다: $HELM_VALUES"

# Helm lint (빠른 검증)
info "helm lint 실행 중…"
helm lint "$HELM_CHART" -f "$HELM_VALUES" --quiet \
  || die "helm lint 실패 — chart 오류 수정 후 재시도"
success "helm lint 통과"

# ── 2. ECR 이미지 존재 확인 ───────────────────────────────────────────────────
info "=== [2/6] ECR 이미지 확인 ==="

ECR_REPO_PREFIX="${ECR_REGISTRY}/agentoe-staging"
FULL_IMAGE="${ECR_REPO_PREFIX}/ops-portal:${IMAGE_TAG}"

if ! aws ecr describe-images \
    --registry-id "${ECR_REGISTRY%%.*}" \
    --repository-name "agentoe-staging/ops-portal" \
    --image-ids "imageTag=${IMAGE_TAG}" \
    --region "${AWS_REGION}" \
    --output text &>/dev/null; then
  die "ECR 이미지를 찾을 수 없습니다: ${FULL_IMAGE}\n  portal-build.yml CI 가 완료됐는지 확인하세요."
fi
success "ECR 이미지 확인: ${FULL_IMAGE}"

# ── 3. ECR 인증 + Kubernetes namespace 준비 ───────────────────────────────────
info "=== [3/6] ECR 인증 + namespace 준비 ==="

if ! $DRY_RUN; then
  aws ecr get-login-password --region "${AWS_REGION}" \
    | kubectl create secret docker-registry ecr-staging \
        --docker-server="${ECR_REGISTRY}" \
        --docker-username=AWS \
        --docker-password-stdin \
        --namespace="${HELM_NAMESPACE}" \
        --dry-run=client -o yaml \
    | kubectl apply -f - \
    || warn "ECR imagePullSecret 갱신 실패 (이미 최신일 수 있음)"

  kubectl create namespace "${HELM_NAMESPACE}" \
    --dry-run=client -o yaml | kubectl apply -f - || true
fi
success "namespace '${HELM_NAMESPACE}' 준비 완료"

# ── 4. Helm upgrade --install ─────────────────────────────────────────────────
info "=== [4/6] Helm upgrade --install ==="

HELM_ARGS=(
  upgrade --install "${HELM_RELEASE}" "${HELM_CHART}"
  --namespace "${HELM_NAMESPACE}"
  -f "${HELM_VALUES}"
  --set "image.repository=${ECR_REPO_PREFIX}/ops-portal"
  --set "image.tag=${IMAGE_TAG}"
  --set "image.pullPolicy=Always"
  --atomic
  --timeout 5m
  --cleanup-on-fail
  --history-max 5
)

if $DRY_RUN; then
  HELM_ARGS+=(--dry-run)
  warn "DRY-RUN 모드 — 실제 배포가 실행되지 않습니다"
fi

info "helm ${HELM_ARGS[*]}"
helm "${HELM_ARGS[@]}"
success "Helm 배포 완료 (release: ${HELM_RELEASE})"

# dry-run 이면 이후 단계 생략
if $DRY_RUN; then
  info "Dry-run 완료 — 실제 배포 없이 종료"
  exit 0
fi

# ── 5. Rollout 대기 ───────────────────────────────────────────────────────────
info "=== [5/6] Rollout 상태 확인 ==="

kubectl rollout status deployment "${HELM_RELEASE}" \
  --namespace="${HELM_NAMESPACE}" \
  --timeout=3m \
  || die "Rollout 실패 — 'kubectl describe pods -n ${HELM_NAMESPACE}' 로 원인 확인"

READY="$(kubectl get deployment "${HELM_RELEASE}" \
  -n "${HELM_NAMESPACE}" \
  -o jsonpath='{.status.readyReplicas}')"
DESIRED="$(kubectl get deployment "${HELM_RELEASE}" \
  -n "${HELM_NAMESPACE}" \
  -o jsonpath='{.spec.replicas}')"
success "Deployment ${READY}/${DESIRED} pods ready"

# ── 6. Smoke test (healthz) ───────────────────────────────────────────────────
info "=== [6/6] Smoke test ==="

if $SKIP_SMOKE; then
  warn "smoke test 생략 (--skip-smoke 플래그)"
else
  SMOKE_URL="https://${PORTAL_HOSTNAME}/healthz"
  info "smoke URL: ${SMOKE_URL}"

  for i in $(seq 1 "${SMOKE_RETRIES}"); do
    HTTP_STATUS="$(curl -sk -o /dev/null -w '%{http_code}' \
      --max-time 5 "${SMOKE_URL}" || echo 000)"
    if [[ "$HTTP_STATUS" == "200" ]]; then
      success "smoke test 통과 (HTTP ${HTTP_STATUS}) — 시도 ${i}/${SMOKE_RETRIES}"
      break
    fi
    if [[ "$i" -eq "${SMOKE_RETRIES}" ]]; then
      die "smoke test 실패 (HTTP ${HTTP_STATUS}) — ${SMOKE_RETRIES}회 재시도 초과"
    fi
    info "HTTP ${HTTP_STATUS} — 5초 후 재시도 (${i}/${SMOKE_RETRIES})…"
    sleep 5
  done
fi

# ── 완료 요약 ─────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}╔══════════════════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║   배포 완료                                          ║${NC}"
echo -e "${GREEN}╚══════════════════════════════════════════════════════╝${NC}"
info "release    : ${HELM_RELEASE}"
info "image      : ${FULL_IMAGE}"
info "namespace  : ${HELM_NAMESPACE}"
info "portal URL : https://${PORTAL_HOSTNAME}"
echo ""

# ── Slack 알림 (선택) ────────────────────────────────────────────────────────
if [[ -n "${SLACK_WEBHOOK_URL:-}" ]]; then
  curl -s -X POST "${SLACK_WEBHOOK_URL}" \
    -H 'Content-type: application/json' \
    -d "{\"text\":\"✅ agentoe-portal staging 배포 완료\\n• image: \`${IMAGE_TAG}\`\\n• URL: https://${PORTAL_HOSTNAME}\"}" \
    &>/dev/null && info "Slack 알림 전송 완료" || warn "Slack 알림 전송 실패"
fi
