#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# nginx 시작 전, ConfigMap 으로 마운트된 env.js.tmpl 을 envsubst 처리해
# 정적 파일 디렉토리에 내려놓는다.
#
# Helm 이 마운트하는 위치: /etc/nginx/runtime-env/env.js.tmpl
# 결과: /usr/share/nginx/html/env.js  (브라우저가 fetch)
#
# readOnlyRootFilesystem 호환 — html/ 만 nginx user 쓰기 가능, /tmp 도 emptyDir.
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

TMPL=/etc/nginx/runtime-env/env.js.tmpl
DEST=/usr/share/nginx/html/env.js

if [[ -f "$TMPL" ]]; then
  echo "[entrypoint] rendering $TMPL → $DEST"
  # envsubst 는 모든 ${VAR} 를 process env 로 치환.
  # 다만 우리 ConfigMap 은 이미 값까지 인라인이므로 cp 로 충분 — 그래도 향후 확장 위해 envsubst.
  envsubst < "$TMPL" > "$DEST"
else
  echo "[entrypoint] WARN: $TMPL not found — fallback to empty env.js"
  cat > "$DEST" <<'EOF'
window.__ENV__ = {};
EOF
fi

chmod 0644 "$DEST"

exec "$@"
