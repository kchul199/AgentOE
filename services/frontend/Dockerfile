# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────────────────────────────────
# agentoe-frontend — production image
#   - Vite build (multi-stage)
#   - nginx unprivileged (UID 101) — Helm 와 일치
#   - 런타임 ConfigMap 의 env.js.tmpl → /usr/share/nginx/html/env.js
#     entrypoint 가 매 부팅 시 envsubst 처리 → 단일 빌드 / 환경별 재배포
# ──────────────────────────────────────────────────────────────────────────

ARG NODE_VERSION=20.12.2
ARG NGINX_VERSION=1.27.0

# ─── Stage 1: build ──────────────────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine AS build

WORKDIR /app

# 의존성 (lockfile 기반 deterministic install)
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# 소스
COPY tsconfig.json vite.config.ts vitest.config.ts ./
COPY index.html ./
COPY src ./src
# public 디렉터리가 있을 수도 / 없을 수도 — 옵셔널 복사
COPY .env* ./
COPY public ./public

# 빌드 — VITE_* 변수는 빌드 시 inject 되지만, 우리는 런타임 주입을 우선시.
ENV NODE_ENV=production
RUN npm run build       # → /app/dist

# ─── Stage 2: runtime (nginxinc/nginx-unprivileged) ──────────────────────
FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine AS runtime

# nginx-unprivileged 는 기본 UID 101 / WORKDIR /usr/share/nginx/html
USER 0
RUN apk add --no-cache gettext bash curl tini \
 && rm -rf /var/cache/apk/*

# 우리 nginx config — SPA fallback + /env.js no-cache + /healthz
COPY --chown=101:101 nginx.conf /etc/nginx/conf.d/default.conf

# Vite 빌드 산출물
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html

# entrypoint — env.js.tmpl 을 envsubst 로 렌더 후 nginx 실행
COPY --chown=101:101 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

USER 101
EXPOSE 8080

ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

LABEL org.opencontainers.image.title="agentoe-frontend"
