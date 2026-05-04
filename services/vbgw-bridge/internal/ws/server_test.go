package ws

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestInternalHandler_Health(t *testing.T) {
	s := &Server{}
	handler := s.InternalHandler()

	req := httptest.NewRequest("GET", "/internal/health", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var resp map[string]string
	json.NewDecoder(w.Body).Decode(&resp)
	if resp["status"] != "healthy" {
		t.Fatalf("expected healthy, got %s", resp["status"])
	}
}

func TestInternalHandler_AIPause_NoSession(t *testing.T) {
	s := &Server{}
	handler := s.InternalHandler()

	req := httptest.NewRequest("POST", "/internal/ai-pause/nonexistent-uuid", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	// Should return 200 even if session doesn't exist (fire-and-forget)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestInternalHandler_AIResume_NoSession(t *testing.T) {
	s := &Server{}
	handler := s.InternalHandler()

	req := httptest.NewRequest("POST", "/internal/ai-resume/nonexistent-uuid", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestInternalHandler_DTMF_MissingDigit(t *testing.T) {
	s := &Server{}
	handler := s.InternalHandler()

	req := httptest.NewRequest("POST", "/internal/dtmf/test-uuid", strings.NewReader(`{}`))
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestInternalHandler_DTMF_InvalidJSON(t *testing.T) {
	s := &Server{}
	handler := s.InternalHandler()

	req := httptest.NewRequest("POST", "/internal/dtmf/test-uuid", strings.NewReader(`{invalid`))
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestInternalHandler_Shutdown(t *testing.T) {
	s := &Server{}
	handler := s.InternalHandler()

	req := httptest.NewRequest("POST", "/internal/shutdown", nil)
	w := httptest.NewRecorder()
	handler.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}

func TestExtractUUID(t *testing.T) {
	tests := []struct {
		path, prefix, expected string
	}{
		{"/internal/ai-pause/abc-123", "/internal/ai-pause/", "abc-123"},
		{"/internal/dtmf/uuid-456", "/internal/dtmf/", "uuid-456"},
		{"/internal/ai-resume/", "/internal/ai-resume/", ""},
	}

	for _, tc := range tests {
		result := extractUUID(tc.path, tc.prefix)
		if result != tc.expected {
			t.Errorf("extractUUID(%q, %q) = %q, want %q", tc.path, tc.prefix, result, tc.expected)
		}
	}
}
