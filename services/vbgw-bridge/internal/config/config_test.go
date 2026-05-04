package config

import (
	"os"
	"testing"
)

func TestLoad_BridgeDefaults(t *testing.T) {
	os.Unsetenv("WS_PORT")
	os.Unsetenv("AI_GRPC_ADDR")

	cfg := Load()

	if cfg.WSPort != 8090 {
		t.Fatalf("expected WS_PORT=8090, got %d", cfg.WSPort)
	}
	if cfg.InternalPort != 8091 {
		t.Fatalf("expected INTERNAL_PORT=8091, got %d", cfg.InternalPort)
	}
	if cfg.AIGrpcAddr != "127.0.0.1:50051" {
		t.Fatalf("expected AI_GRPC_ADDR=127.0.0.1:50051, got %s", cfg.AIGrpcAddr)
	}
	if cfg.AIGrpcTLS {
		t.Fatal("expected AI_GRPC_TLS=false")
	}
	if cfg.GrpcMaxRetries != 5 {
		t.Fatalf("expected GRPC_MAX_RETRIES=5, got %d", cfg.GrpcMaxRetries)
	}
}

func TestLoad_BridgeOverrides(t *testing.T) {
	os.Setenv("WS_PORT", "9090")
	os.Setenv("AI_GRPC_ADDR", "ai.example.com:50051")
	os.Setenv("AI_GRPC_TLS", "true")
	defer func() {
		os.Unsetenv("WS_PORT")
		os.Unsetenv("AI_GRPC_ADDR")
		os.Unsetenv("AI_GRPC_TLS")
	}()

	cfg := Load()

	if cfg.WSPort != 9090 {
		t.Fatalf("expected WS_PORT=9090, got %d", cfg.WSPort)
	}
	if cfg.AIGrpcAddr != "ai.example.com:50051" {
		t.Fatalf("expected ai.example.com:50051, got %s", cfg.AIGrpcAddr)
	}
	if !cfg.AIGrpcTLS {
		t.Fatal("expected AI_GRPC_TLS=true")
	}
}
