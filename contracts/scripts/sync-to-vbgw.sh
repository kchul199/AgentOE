#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# sync-to-vbgw.sh
#
# AgenticOE_v2/skeleton/contracts/proto/voicebot.proto 를
# vbgw_v2 의 3 곳에 복사하면서 go_package 만 consumer 별로 치환.
#
# 사용:
#   ./sync-to-vbgw.sh /Users/kchul199/vbgw_v2
#
# 동기화 대상:
#   1) vbgw-ai/proto/voicebot.proto                        go_package=vbgw-ai/proto/voicebot
#   2) vbgw-freeswitch/protos/voicebot.proto               go_package=vbgw-bridge/proto/voicebot
#   3) vbgw-freeswitch/bridge/proto/voicebot/voicebot.proto (참고: bridge 가 stub 직접 가짐)
#                                                          go_package=vbgw-bridge/proto/voicebot
#
# 동기화 전 백업: 대상 파일에 .bak 저장 (drift 검증용).
# 변경 없으면 no-op.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_PROTO="${SCRIPT_DIR}/../proto/voicebot.proto"

VBGW="${1:-}"
if [[ -z "$VBGW" ]]; then
  echo "사용: $0 <path-to-vbgw_v2>" >&2
  exit 1
fi
[[ -d "$VBGW" ]] || { echo "디렉토리 없음: $VBGW" >&2; exit 1; }
[[ -f "$SRC_PROTO" ]] || { echo "source proto 없음: $SRC_PROTO" >&2; exit 1; }

# 대상별 (path → go_package) 매핑
declare -a TARGETS=(
  "vbgw-ai/proto/voicebot.proto|vbgw-ai/proto/voicebot"
  "vbgw-freeswitch/protos/voicebot.proto|vbgw-bridge/proto/voicebot"
  "vbgw-freeswitch/bridge/proto/voicebot/voicebot.proto|vbgw-bridge/proto/voicebot"
)

WARN_FOOTER='
// ───────────────────────────────────────────────────────────────────────
// AUTO-GENERATED — DO NOT EDIT HERE.
// Canonical source: AgenticOE_v2/skeleton/contracts/proto/voicebot.proto
// Sync via:        AgenticOE_v2/skeleton/contracts/  →  make sync-vbgw VBGW=...
// ───────────────────────────────────────────────────────────────────────
'

CHANGED=0
for entry in "${TARGETS[@]}"; do
  TARGET_REL="${entry%%|*}"
  GO_PKG="${entry##*|}"
  TARGET="$VBGW/$TARGET_REL"

  mkdir -p "$(dirname "$TARGET")"

  # 임시 파일에 변환된 내용 작성
  TMP="$(mktemp)"
  awk -v gp="$GO_PKG" '
    /^option go_package =/ { printf "option go_package = \"%s\";\n", gp; next }
    { print }
  ' "$SRC_PROTO" > "$TMP"
  printf "%s" "$WARN_FOOTER" >> "$TMP"

  if [[ -f "$TARGET" ]] && cmp -s "$TMP" "$TARGET"; then
    echo "[skip] $TARGET_REL — unchanged"
    rm -f "$TMP"
  else
    [[ -f "$TARGET" ]] && cp "$TARGET" "$TARGET.bak"
    mv "$TMP" "$TARGET"
    echo "[sync] $TARGET_REL  (go_package=$GO_PKG)"
    CHANGED=$((CHANGED+1))
  fi
done

echo
if [[ $CHANGED -eq 0 ]]; then
  echo "[done] no changes — vbgw proto 가 이미 canonical 과 동일."
else
  echo "[done] $CHANGED 개 파일 동기화."
  echo "       다음 단계: vbgw_v2 안에서 stub 재생성 (각 컴포넌트의 빌드 시스템 따라):"
  echo "         cd $VBGW/vbgw-ai && protoc ... (각 .Dockerfile 참고)"
  echo "         cd $VBGW/vbgw-freeswitch/bridge && protoc ..."
  echo "       그리고 vbgw_v2 측에서 git diff 검증 후 별도 PR."
fi
