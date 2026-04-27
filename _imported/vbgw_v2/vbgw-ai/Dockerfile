FROM golang:1.23-alpine AS builder

WORKDIR /app

# Install build dependencies
RUN apk add --no-cache git

# Copy go mod and sum files
COPY go.mod ./
# RUN go mod download # Skipped because we don't have go.sum yet, will fetch during build

# Copy the source code
COPY . .

# Build the application
RUN go mod tidy
RUN CGO_ENABLED=0 GOOS=linux go build -o vbgw-ai ./cmd/main.go

# Run stage
FROM alpine:3.18

WORKDIR /root/

# Copy the binary from builder
COPY --from=builder /app/vbgw-ai .

# Expose gRPC port
EXPOSE 8091

# Set the entry point
CMD ["./vbgw-ai"]
