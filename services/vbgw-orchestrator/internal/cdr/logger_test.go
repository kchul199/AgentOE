package cdr

import (
	"testing"
	"time"

	"vbgw-orchestrator/internal/session"
)

func TestLogHangup_SetsDuration(t *testing.T) {
	s := session.NewSession("s1", "fs1", "010", "100")
	// Simulate 5 second call
	s.CreatedAt = time.Now().Add(-5 * time.Second)
	s.SetAnsweredAt(time.Now().Add(-4 * time.Second))

	// Should not panic
	LogHangup(s, "NORMAL_CLEARING")

	if s.HangupAt.IsZero() {
		t.Fatal("expected HangupAt to be set")
	}
}

func TestLogHangup_WithBridgedWith(t *testing.T) {
	s := session.NewSession("s1", "fs1", "010", "100")
	s.SetBridgedWith("s2")

	LogHangup(s, "NORMAL_CLEARING")

	if s.BridgedWith() != "s2" {
		t.Fatalf("expected BridgedWith=s2, got %s", s.BridgedWith())
	}
}

func TestLogHangup_ZeroDuration(t *testing.T) {
	s := session.NewSession("s1", "fs1", "010", "100")
	// Call created and immediately hung up
	LogHangup(s, "ORIGINATOR_CANCEL")
	// Should not panic, duration ~0
}
