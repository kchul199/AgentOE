# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────────────────────────────────
# agentoe-backend — production multi-stage image
#
# 설계 포인트:
#   1) 빌드 의존성 (gcc 등) 은 builder 단계에만 → 최종 이미지 슬림
#   2) 비-root UID 10001 (helm podSecurityContext 와 일치)
#   3) /app 은 root:0 소유, 코드는 read-only 로 mount 가능
#   4) /tmp 만 쓰기 — readOnlyRootFilesystem 호환
#   5) uvicorn workers 는 환경변수로 외부에서 조정
# ──────────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.11.9

# ─── Stage 1: builder ─────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# wheel 빌드용 toolchain (production 단계로는 안 넘어감)
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential gcc \
 && rm -rf /var/lib/apt/lists/*

# 의존성 먼저 설치 → 캐시 효율
COPY pyproject.toml ./
COPY app ./app
RUN pip install --prefix=/install ".[runtime]" 2>/dev/null \
 || pip install --prefix=/install .

# ─── Stage 2: runtime ─────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONPATH=/app \
    UVICORN_WORKERS=2 \
    UVICORN_HOST=0.0.0.0 \
    UVICORN_PORT=8000 \
    # N1.12 — SSE 포함 동시 HTTP 연결 상한 (0 = 무제한).
    # prod Helm values 에서 UVICORN_LIMIT_CONCURRENCY=500 으로 오버라이드.
    UVICORN_LIMIT_CONCURRENCY=0

# 비-root user (Helm 의 runAsUser=10001 과 동일)
RUN groupadd --system --gid 10001 agentoe \
 && useradd  --system --uid 10001 --gid agentoe --home /app --shell /sbin/nologin agentoe

# minimal 런타임 deps — TLS / curl (livez probe)
RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

# builder 결과물만 복사
COPY --from=builder /install /usr/local

# 앱 코드
WORKDIR /app
COPY app ./app
COPY pyproject.toml ./

# 권한 — 코드는 root 소유, agentoe 는 read-only (readOnlyRootFilesystem 친화)
RUN chown -R root:root /app && chmod -R a+rX /app

USER 10001:10001
EXPOSE 8000

# tini = PID 1 → SIGTERM → uvicorn graceful shutdown 정상 동작.
ENTRYPOINT ["tini", "--"]
# UVICORN_LIMIT_CONCURRENCY=0 → --limit-concurrency 플래그 생략 (무제한).
# 0 이 아닌 값이면 플래그 추가 → SSE long-lived 연결 포함 계산.
CMD ["sh", "-c", "\
  EXTRA=''; \
  if [ \"${UVICORN_LIMIT_CONCURRENCY}\" != '0' ]; then \
    EXTRA=\"--limit-concurrency ${UVICORN_LIMIT_CONCURRENCY}\"; \
  fi; \
  exec uvicorn app.main:app \
    --host ${UVICORN_HOST} \
    --port ${UVICORN_PORT} \
    --workers ${UVICORN_WORKERS} \
    --proxy-headers \
    --forwarded-allow-ips='*' \
    $EXTRA \
"]

# ─── healthcheck (compose 환경 — k8s 는 probe 따로) ─────────────────────
HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/api/v1/livez || exit 1

# ─── OCI metadata ──────────────────────────────────────────────────────────
LABEL org.opencontainers.image.title="agentoe-backend" \
      org.opencontainers.image.source="https://github.com/agentoe/agentoe" \
      org.opencontainers.image.licenses="UNLICENSED"
