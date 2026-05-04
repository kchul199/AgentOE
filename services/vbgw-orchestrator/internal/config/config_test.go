package config

import (
	"os"
	"testing"
)

func TestLoad_Defaults(t *testing.T) {
	// Clear any env vars that might interfere
	os.Unsetenv("ESL_HOST")
	os.Unsetenv("HTTP_PORT")
	os.Unsetenv("MAX_SESSIONS")

	cfg := Load()

	if cfg.ESLHost != "127.0.0.1" {
		t.Fatalf("expected ESL_HOST=127.0.0.1, got %s", cfg.ESLHost)
	}
	if cfg.ESLPort != 8021 {
		t.Fatalf("expected ESL_PORT=8021, got %d", cfg.ESLPort)
	}
	if cfg.HTTPPort != 8080 {
		t.Fatalf("expected HTTP_PORT=8080, got %d", cfg.HTTPPort)
	}
	if cfg.MaxSessions != 100 {
		t.Fatalf("expected MAX_SESSIONS=100, got %d", cfg.MaxSessions)
	}
	if cfg.RecordingEnable {
		t.Fatal("expected RECORDING_ENABLE=false")
	}
}

func TestLoad_EnvOverrides(t *testing.T) {
	os.Setenv("HTTP_PORT", "9090")
	os.Setenv("MAX_SESSIONS", "50")
	os.Setenv("RECORDING_ENABLE", "true")
	defer func() {
		os.Unsetenv("HTTP_PORT")
		os.Unsetenv("MAX_SESSIONS")
		os.Unsetenv("RECORDING_ENABLE")
	}()

	cfg := Load()

	if cfg.HTTPPort != 9090 {
		t.Fatalf("expected HTTP_PORT=9090, got %d", cfg.HTTPPort)
	}
	if cfg.MaxSessions != 50 {
		t.Fatalf("expected MAX_SESSIONS=50, got %d", cfg.MaxSessions)
	}
	if !cfg.RecordingEnable {
		t.Fatal("expected RECORDING_ENABLE=true")
	}
}

func TestLoad_InvalidEnvFallsBackToDefault(t *testing.T) {
	os.Setenv("HTTP_PORT", "not_a_number")
	os.Setenv("RECORDING_ENABLE", "not_a_bool")
	defer func() {
		os.Unsetenv("HTTP_PORT")
		os.Unsetenv("RECORDING_ENABLE")
	}()

	cfg := Load()

	if cfg.HTTPPort != 8080 {
		t.Fatalf("expected fallback HTTP_PORT=8080, got %d", cfg.HTTPPort)
	}
	if cfg.RecordingEnable {
		t.Fatal("expected fallback RECORDING_ENABLE=false")
	}
}
