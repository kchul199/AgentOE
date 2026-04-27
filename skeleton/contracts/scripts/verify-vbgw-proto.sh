#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# verify-vbgw-proto.sh
#
# vbgw_v2 의 3 곳 .proto 가 우리 canonical 과 일치하는지 검증.
# go_package 만 의도된 차이 — 그 외 차이 발견 시 exit 1 (CI 실패).
#
# 사용:
#   ./verify-vbgw-proto.sh /Users/kchul199/vbgw_v2
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC_PROTO="${SCRIPT_DIR}/../proto/voicebot.proto"
VBGW="${1:?usage: $0 <path-to-vbgw_v2>}"

declare -a TARGETS=(
  "vbgw-ai/proto/voicebot.proto"
  "vbgw-freeswitch/protos/voicebot.proto"
  "vbgw-freeswitch/bridge/proto/voicebot/voicebot.proto"
)

# 의미 있는 차이만 비교. 다음은 무시:
#   - go_package (consumer 별로 의도된 차이)
#   - 모든 주석 (// ... 와 /* ... */ 한 줄)
#   - 빈 줄과 trailing whitespace
# 결과: service / message / field / option (go_package 제외) 만 남음.
normalize() {
  awk '
    /^[[:space:]]*option go_package =/ { next }      # go_package 무시
    /^[[:space:]]*\/\// { next }                     # // 주석 한 줄
    /^[[:space:]]*\/\*.*\*\/[[:space:]]*$/ { next }  # /* */ 한 줄
    /^[[:space:]]*$/ { next }                        # 빈 줄
    {
      # 줄 안 인라인 주석 제거 + trailing whitespace strip
      sub(/[[:space:]]*\/\/.*$/, "")
      sub(/[[:space:]]+$/, "")
      sub(/^[[:space:]]+/, "")
      gsub(/[[:space:]]+/, " ")
      print
    }
  ' "$1"
}

ERR=0
for REL in "${TARGETS[@]}"; do
  TGT="$VBGW/$REL"
  if [[ ! -f "$TGT" ]]; then
    echo "[MISS] $REL — 파일 없음. sync-to-vbgw.sh 실행 필요."
    ERR=$((ERR+1))
    continue
  fi
  if diff -q <(normalize "$SRC_PROTO") <(normalize "$TGT") >/dev/null; then
    echo "[OK]   $REL"
  else
    echo "[DIFF] $REL — canonical 과 불일치:"
    diff <(normalize "$SRC_PROTO") <(normalize "$TGT") | sed 's/^/         /' || true
    ERR=$((ERR+1))
  fi
done

echo
if [[ $ERR -eq 0 ]]; then
  echo "[done] 모든 vbgw proto 가 canonical 과 일치."
  exit 0
else
  echo "[done] $ERR 개 불일치 — sync-to-vbgw.sh 실행 후 vbgw 측 stub 재생성."
  exit 1
fi
