# orchestrator/Dockerfile
# 변경이력: v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성
FROM golang:1.22-alpine AS builder
WORKDIR /app
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o /orchestrator ./cmd/main.go

FROM alpine:3.19
RUN apk add --no-cache ca-certificates wget
COPY --from=builder /orchestrator /usr/local/bin/orchestrator
USER nobody:nobody
ENTRYPOINT ["orchestrator"]
