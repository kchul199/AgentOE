# bridge/Dockerfile
# 변경이력: v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성
# 변경이력: v1.1.0 | 2026-04-07 | [Implementer] | Phase 2 | CGO_ENABLED=1 + onnxruntime

FROM golang:1.22-alpine AS builder
RUN apk add --no-cache gcc musl-dev wget

# Install ONNX Runtime shared library
ARG ORT_VERSION=1.17.1
RUN wget -q https://github.com/microsoft/onnxruntime/releases/download/v${ORT_VERSION}/onnxruntime-linux-x64-${ORT_VERSION}.tgz \
    && tar -xzf onnxruntime-linux-x64-${ORT_VERSION}.tgz \
    && cp onnxruntime-linux-x64-${ORT_VERSION}/lib/libonnxruntime.so* /usr/local/lib/ \
    && cp -r onnxruntime-linux-x64-${ORT_VERSION}/include/* /usr/local/include/ \
    && rm -rf onnxruntime-linux-x64-${ORT_VERSION}*

WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=1 GOOS=linux go build -o /bridge ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates wget libstdc++
# ONNX Runtime shared library
COPY --from=builder /usr/local/lib/libonnxruntime.so* /usr/local/lib/
ENV LD_LIBRARY_PATH=/usr/local/lib
COPY --from=builder /bridge /usr/local/bin/bridge
USER nobody:nobody
ENTRYPOINT ["bridge"]
