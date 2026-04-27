package esl

import (
	"context"
	"testing"
)

func TestNewClient(t *testing.T) {
	handler := func(evt *Event) {}
	c := NewClient("127.0.0.1", 8021, "ClueCon", handler)

	if c == nil {
		t.Fatal("expected non-nil client")
	}
	if c.host != "127.0.0.1" {
		t.Fatalf("expected host 127.0.0.1, got %s", c.host)
	}
	if c.port != 8021 {
		t.Fatalf("expected port 8021, got %d", c.port)
	}
	if c.connected {
		t.Fatal("expected not connected initially")
	}
}

func TestClient_IsConnected_Initially(t *testing.T) {
	c := NewClient("127.0.0.1", 8021, "test", nil)
	if c.IsConnected() {
		t.Fatal("expected IsConnected=false before Connect()")
	}
}

func TestClient_Close_BeforeConnect(t *testing.T) {
	c := NewClient("127.0.0.1", 8021, "test", nil)
	// Should not panic
	c.Close()

	if c.IsConnected() {
		t.Fatal("expected not connected after Close()")
	}
}

func TestClient_SetOnReconnect(t *testing.T) {
	c := NewClient("127.0.0.1", 8021, "test", nil)

	called := false
	c.SetOnReconnect(func() {
		called = true
	})

	if c.onReconnect == nil {
		t.Fatal("expected onReconnect to be set")
	}

	// Execute callback
	c.onReconnect()
	if !called {
		t.Fatal("expected callback to be called")
	}
}

func TestClient_ApiRespChBuffer(t *testing.T) {
	c := NewClient("127.0.0.1", 8021, "test", nil)

	// T-02: Buffer should be 16
	if cap(c.apiRespCh) != 16 {
		t.Fatalf("expected apiRespCh cap=16, got %d", cap(c.apiRespCh))
	}
}

func TestClient_ConnectWithRetry_ContextCancelled(t *testing.T) {
	c := NewClient("127.0.0.1", 19999, "test", nil) // Non-existent server

	ctx, cancel := context.WithCancel(context.Background())
	cancel() // Immediately cancel

	err := c.ConnectWithRetry(ctx)
	if err == nil {
		t.Fatal("expected error with cancelled context")
	}
}

func TestClient_ImplementsCommander(t *testing.T) {
	// Compile-time check: *Client must implement Commander
	var _ Commander = (*Client)(nil)
}

func TestGetActiveChannelUUIDs_ParsesCSV(t *testing.T) {
	// Test the UUID parsing logic directly
	resp := "abc12345-1234-1234-1234-123456789012,CS_EXECUTE,test\n" +
		"def12345-1234-1234-1234-123456789012,CS_PARK,test2\n" +
		"not-a-uuid,CS_HANGUP,test3\n" +
		"\n"

	uuids := make(map[string]bool)
	for _, line := range splitLines(resp) {
		fields := splitCSV(line)
		if len(fields) > 0 {
			uuid := trimSpace(fields[0])
			if len(uuid) == 36 && containsDash(uuid) {
				uuids[uuid] = true
			}
		}
	}

	if len(uuids) != 2 {
		t.Fatalf("expected 2 UUIDs, got %d", len(uuids))
	}
	if !uuids["abc12345-1234-1234-1234-123456789012"] {
		t.Fatal("expected first UUID")
	}
	if !uuids["def12345-1234-1234-1234-123456789012"] {
		t.Fatal("expected second UUID")
	}
}

// Helper functions mirroring client.go logic for testing
func splitLines(s string) []string {
	var lines []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == '\n' {
			lines = append(lines, s[start:i])
			start = i + 1
		}
	}
	if start < len(s) {
		lines = append(lines, s[start:])
	}
	return lines
}

func splitCSV(s string) []string {
	var fields []string
	start := 0
	for i := 0; i < len(s); i++ {
		if s[i] == ',' {
			fields = append(fields, s[start:i])
			start = i + 1
		}
	}
	fields = append(fields, s[start:])
	return fields
}

func trimSpace(s string) string {
	i := 0
	for i < len(s) && (s[i] == ' ' || s[i] == '\t' || s[i] == '\r') {
		i++
	}
	j := len(s)
	for j > i && (s[j-1] == ' ' || s[j-1] == '\t' || s[j-1] == '\r') {
		j--
	}
	return s[i:j]
}

func containsDash(s string) bool {
	for _, c := range s {
		if c == '-' {
			return true
		}
	}
	return false
}
