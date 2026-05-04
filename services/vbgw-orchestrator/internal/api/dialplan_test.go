package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestGenerateDialplan_ValidRequest(t *testing.T) {
	handler := &DialplanHandler{}

	body := strings.NewReader("Hunt-Context=default&Caller-Destination-Number=1004&Caller-Caller-ID-Number=9999")
	req := httptest.NewRequest("POST", "/api/v1/fs/dialplan", body)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	w := httptest.NewRecorder()
	handler.GenerateDialplan(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	respBody := w.Body.String()

	// Verify XML structure
	if !strings.Contains(respBody, `<document type="freeswitch/xml">`) {
		t.Error("response missing XML document root")
	}
	if !strings.Contains(respBody, `<section name="dialplan"`) {
		t.Error("response missing dialplan section")
	}
	if !strings.Contains(respBody, "voicebot-inbound") {
		t.Error("response missing voicebot-inbound extension")
	}
	if !strings.Contains(respBody, "text/xml") {
		// Check Content-Type header
		ct := w.Header().Get("Content-Type")
		if !strings.Contains(ct, "text/xml") {
			t.Errorf("expected text/xml content type, got %s", ct)
		}
	}
}

func TestGenerateDialplan_EmptyContext(t *testing.T) {
	handler := &DialplanHandler{}

	body := strings.NewReader("Caller-Destination-Number=1004")
	req := httptest.NewRequest("POST", "/api/v1/fs/dialplan", body)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	w := httptest.NewRecorder()
	handler.GenerateDialplan(w, req)

	// Should still return valid XML (default context handling)
	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}
}
