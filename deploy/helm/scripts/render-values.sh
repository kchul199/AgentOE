#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# Terraform output 으로 values/<env>/*.values.yaml 의 REPLACE_* 치환.
#
# 의존: jq, sed
# 입력: 환경 이름 ($1), 옵션으로 terraform output JSON 파일 경로 ($2)
# 동작: in-place 치환 — 사용 전 git diff 로 결과 확인 권장.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

ENV="${1:?usage: $0 <env> [tf-output.json]}"
TF_JSON="${2:-/tmp/agentoe-tf-${ENV}.json}"

if [[ ! -f "$TF_JSON" ]]; then
  echo "[render-values] $TF_JSON 가 없음. terraform output -json 으로 먼저 생성:" >&2
  echo "  terraform -chdir=../terraform/environments/${ENV} output -json > ${TF_JSON}" >&2
  exit 1
fi

VALUES_DIR="$(dirname "$0")/../values/${ENV}"
[[ -d "$VALUES_DIR" ]] || { echo "[render-values] no dir: $VALUES_DIR"; exit 1; }

declare -A MAP
MAP[REPLACE_ECR_REGISTRY]="$(jq -r '.ecr_registry.value // empty' "$TF_JSON")"
MAP[REPLACE_ATLAS_SRV_HOST]="$(jq -r '.atlas_srv_host.value // empty' "$TF_JSON")"
MAP[REPLACE_ATLAS_SRV_HOST_PROD]="$(jq -r '.atlas_srv_host.value // empty' "$TF_JSON")"
MAP[REPLACE_REDIS_PRIMARY_HOST]="$(jq -r '.redis_primary_host.value // empty' "$TF_JSON")"
MAP[REPLACE_REDIS_PRIMARY_HOST_PROD]="$(jq -r '.redis_primary_host.value // empty' "$TF_JSON")"
MAP[REPLACE_BACKEND_IRSA_ROLE_ARN]="$(jq -r '.backend_irsa_role_arn.value // empty' "$TF_JSON")"
MAP[REPLACE_BACKEND_IRSA_ROLE_ARN_PROD]="$(jq -r '.backend_irsa_role_arn.value // empty' "$TF_JSON")"
MAP[REPLACE_VBGW_IRSA_ROLE_ARN]="$(jq -r '.vbgw_irsa_role_arn.value // empty' "$TF_JSON")"
MAP[REPLACE_VBGW_IRSA_ROLE_ARN_PROD]="$(jq -r '.vbgw_irsa_role_arn.value // empty' "$TF_JSON")"
MAP[REPLACE_ACM_ARN_STAGING]="$(jq -r '.acm_certificate_arn.value // empty' "$TF_JSON")"
MAP[REPLACE_ACM_ARN_PROD]="$(jq -r '.acm_certificate_arn.value // empty' "$TF_JSON")"
MAP[REPLACE_WAF_ACL_ARN]="$(jq -r '.waf_acl_arn.value // empty' "$TF_JSON")"
MAP[REPLACE_ALB_ACCESSLOG_BUCKET]="$(jq -r '.alb_log_bucket.value // empty' "$TF_JSON")"
MAP[REPLACE_NLB_ACCESSLOG_BUCKET]="$(jq -r '.nlb_log_bucket.value // empty' "$TF_JSON")"
MAP[REPLACE_SENTRY_DSN_PROD]="$(jq -r '.sentry_dsn_prod.value // empty' "$TF_JSON")"

for FILE in "$VALUES_DIR"/*.values.yaml; do
  [[ -f "$FILE" ]] || continue
  echo "[render-values] $FILE"
  for KEY in "${!MAP[@]}"; do
    VAL="${MAP[$KEY]}"
    [[ -z "$VAL" ]] && continue
    # sed BSD/GNU 호환 — 백슬래시/슬래시 들어간 값도 안전하게 치환.
    ESCAPED="$(printf '%s\n' "$VAL" | sed 's/[\/&]/\\&/g')"
    sed -i.bak "s/${KEY}/${ESCAPED}/g" "$FILE"
    rm -f "${FILE}.bak"
  done
done

echo "[render-values] done. 결과 검증: git diff $VALUES_DIR"
