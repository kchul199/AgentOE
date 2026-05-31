#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────
# ops-portal nginx 부팅 전 초기화 (Phase N — N2.6)
#
# 1) nginx.conf.tmpl → envsubst → /etc/nginx/conf.d/default.conf
#    BACKEND_UPSTREAM (k8s service DNS) 을 런타임 주입.
# 2) env.js.tmpl → envsubst → /usr/share/nginx/html/env.js (SPA 런타임 변수)
#    ConfigMap 으로 마운트: /etc/nginx/runtime-env/
#
# readOnlyRootFilesystem 호환:
#   /etc/nginx/conf.d/  → emptyDir (writeable, nginx config 렌더 대상)
#   /usr/share/nginx/html/ → 빌드 산출물 (UID 101 소유)
#   /var/cache/nginx, /var/run, /tmp → emptyDir
# ──────────────────────────────────────────────────────────────────────────
set -euo pipefail

RUNTIME_DIR=/etc/nginx/runtime-env
NGINX_CONF_TMPL="${RUNTIME_DIR}/default.conf.tmpl"
NGINX_CONF_DEST=/etc/nginx/conf.d/default.conf
ENV_JS_TMPL="${RUNTIME_DIR}/env.js.tmpl"
ENV_JS_DEST=/usr/share/nginx/html/env.js

# ── 1) nginx.conf 렌더 ──────────────────────────────────────────────────
if [[ -f "$NGINX_CONF_TMPL" ]]; then
  echo "[entrypoint] rendering nginx config: $NGINX_CONF_TMPL → $NGINX_CONF_DEST"
  envsubst '${BACKEND_UPSTREAM}' < "$NGINX_CONF_TMPL" > "$NGINX_CONF_DEST"
else
  echo "[entrypoint] WARN: $NGINX_CONF_TMPL not found — using baked-in nginx.conf"
fi

# ── 2) env.js 렌더 (optional) ──────────────────────────────────────────
if [[ -f "$ENV_JS_TMPL" ]]; then
  echo "[entrypoint] rendering env.js: $ENV_JS_TMPL → $ENV_JS_DEST"
  envsubst < "$ENV_JS_TMPL" > "$ENV_JS_DEST"
else
  echo "[entrypoint] INFO: no env.js.tmpl — writing empty stub"
  cat > "$ENV_JS_DEST" <<'EOF'
window.__ENV__ = {};
EOF
fi

chmod 0644 "$ENV_JS_DEST"

exec "$@"
