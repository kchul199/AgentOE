# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────────────────────────────────
# agentoe-ops-portal — production image (Phase N — N2.6)
#
# Stage 1 : Node 20 — tsc + vite build → /app/dist
# Stage 2 : nginx-unprivileged (UID 101) — 정적 파일 서빙 + /api 역방향 프록시
#
# 런타임 주입 (Helm ConfigMap → emptyDir mount):
#   BACKEND_UPSTREAM — /etc/nginx/runtime-env/default.conf.tmpl 의 ${BACKEND_UPSTREAM}
#                      docker-entrypoint.sh 가 envsubst 처리 후 conf.d 에 배포.
# ──────────────────────────────────────────────────────────────────────────

ARG NODE_VERSION=20.12.2
ARG NGINX_VERSION=1.27.0

# ─── Stage 1: build ───────────────────────────────────────────────────────
FROM node:${NODE_VERSION}-alpine AS build

WORKDIR /app

# 의존성 (lockfile 기반 deterministic install)
COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

# 소스 복사
COPY tsconfig.json vite.config.ts ./
COPY index.html ./
COPY src ./src
COPY .env* ./

# 빌드 — VITE_* 변수는 런타임 주입 우선, 빌드 시 BASE_URL 등 최소값만.
ENV NODE_ENV=production
RUN npm run build        # → /app/dist

# ─── Stage 2: runtime ─────────────────────────────────────────────────────
FROM nginxinc/nginx-unprivileged:${NGINX_VERSION}-alpine AS runtime

USER 0
RUN apk add --no-cache gettext bash curl tini \
 && rm -rf /var/cache/apk/*

# nginx config (템플릿 포함 — BACKEND_UPSTREAM 을 envsubst 로 치환)
# ConfigMap 이 없을 경우를 위한 빌드-시 기본값.
COPY --chown=101:101 nginx.conf /etc/nginx/templates/default.conf.tmpl

# entrypoint — conf.d 및 env.js 렌더 후 nginx 기동
COPY --chown=101:101 docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh

# Vite 빌드 산출물
COPY --from=build --chown=101:101 /app/dist /usr/share/nginx/html

USER 101
EXPOSE 8080

# 기본 BACKEND_UPSTREAM (로컬 개발용 — k8s 에서는 ConfigMap 으로 override)
ENV BACKEND_UPSTREAM=http://localhost:8000

ENTRYPOINT ["tini", "--", "/usr/local/bin/docker-entrypoint.sh"]
CMD ["nginx", "-g", "daemon off;"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=5s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz || exit 1

LABEL org.opencontainers.image.title="agentoe-ops-portal"
LABEL org.opencontainers.image.description="AgentOE 통합 운영 포탈 (internal ALB)"
