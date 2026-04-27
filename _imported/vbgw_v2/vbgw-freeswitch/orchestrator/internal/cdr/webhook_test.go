package cdr

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"sync/atomic"
	"testing"
	"time"

	"vbgw-orchestrator/internal/config"
	"vbgw-orchestrator/internal/session"
)

func TestSendAsync_SuccessfulDelivery(t *testing.T) {
	var received atomic.Int32
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		received.Add(1)

		// Verify Content-Type
		if r.Header.Get("Content-Type") != "application/json" {
			t.Error("expected application/json content type")
		}

		var payload WebhookPayload
		if err := json.NewDecoder(r.Body).Decode(&payload); err != nil {
			t.Errorf("invalid JSON payload: %v", err)
		}
		if payload.SessionID != "test-session-1" {
			t.Errorf("expected session_id=test-session-1, got %s", payload.SessionID)
		}
		if payload.Cause != "NORMAL_CLEARING" {
			t.Errorf("expected cause=NORMAL_CLEARING, got %s", payload.Cause)
		}

		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	cfg := &config.Config{CDRWebhookURL: srv.URL}
	s := session.NewSession("node1", "test-session-1", "fs-uuid-1", "01012345678", "1004")
	s.SetAnsweredAt(time.Now().Add(-30 * time.Second))
	s.SetHangupAt(time.Now())

	SendAsync(cfg, s, "NORMAL_CLEARING")

	// Wait for async goroutine
	time.Sleep(200 * time.Millisecond)

	if received.Load() != 1 {
		t.Fatalf("expected 1 webhook delivery, got %d", received.Load())
	}
}

func TestSendAsync_HMACSignature(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		sig := r.Header.Get("X-VBGW-Signature")
		if sig == "" {
			t.Error("expected X-VBGW-Signature header to be present")
		}
		if len(sig) != 64 { // SHA256 hex = 64 chars
			t.Errorf("expected 64 char hex signature, got %d chars", len(sig))
		}
		w.WriteHeader(http.StatusOK)
	}))
	defer srv.Close()

	cfg := &config.Config{
		CDRWebhookURL:    srv.URL,
		CDRWebhookSecret: "test-secret-key-12345",
	}
	s := session.NewSession("node1", "sig-test", "fs-uuid-2", "", "")

	SendAsync(cfg, s, "ORIGINATOR_CANCEL")
	time.Sleep(200 * time.Millisecond)
}

func TestSendAsync_SkipsWhenURLEmpty(t *testing.T) {
	cfg := &config.Config{CDRWebhookURL: ""}
	s := session.NewSession("node1", "skip-test", "fs-uuid-3", "", "")

	// Should return immediately without panic
	SendAsync(cfg, s, "NORMAL_CLEARING")
}

func TestSendAsync_BackupOnServerError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusInternalServerError)
	}))
	defer srv.Close()

	// Override backup dir to temp for test
	origDir := backupDir
	backupDir = t.TempDir()
	defer func() { backupDir = origDir }()

	cfg := &config.Config{CDRWebhookURL: srv.URL}
	s := session.NewSession("node1", "backup-test", "fs-uuid-4", "", "")

	SendAsync(cfg, s, "NORMAL_CLEARING")
	time.Sleep(300 * time.Millisecond)

	// Verify backup file was created
	entries, _ := os.ReadDir(backupDir)
	if len(entries) == 0 {
		t.Fatal("expected CDR backup file to be created on server error")
	}
}
