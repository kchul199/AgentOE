# syntax=docker/dockerfile:1.7
# ──────────────────────────────────────────────────────────────────────────
# agentoe-vbgw — Voice Bridge Gateway production image
#   - Helm: runAsUser=10001, readOnlyRootFilesystem=true, capabilities drop ALL
#   - 4 ports: gRPC 50051 / WS 50052 / health 8080 / metrics 9100
# ──────────────────────────────────────────────────────────────────────────

ARG PYTHON_VERSION=3.11.9

FROM python:${PYTHON_VERSION}-slim AS builder

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build
RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential gcc \
 && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml ./
COPY app ./app
RUN pip install --prefix=/install .

# ─── runtime ──────────────────────────────────────────────────────────────
FROM python:${PYTHON_VERSION}-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app \
    GRPC_PORT=50051 \
    WS_PORT=50052 \
    HEALTH_PORT=8080 \
    METRICS_PORT=9100

RUN groupadd --system --gid 10001 agentoe \
 && useradd  --system --uid 10001 --gid agentoe --home /app --shell /sbin/nologin agentoe

RUN apt-get update \
 && apt-get install -y --no-install-recommends ca-certificates curl tini \
 && rm -rf /var/lib/apt/lists/*

COPY --from=builder /install /usr/local

WORKDIR /app
COPY app ./app
COPY pyproject.toml ./
RUN chown -R root:root /app && chmod -R a+rX /app

USER 10001:10001

EXPOSE 50051 50052 8080 9100

ENTRYPOINT ["tini", "--"]
CMD ["python", "-m", "app.main"]

HEALTHCHECK --interval=15s --timeout=3s --start-period=10s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8080/healthz/live || exit 1

LABEL org.opencontainers.image.title="agentoe-vbgw"
