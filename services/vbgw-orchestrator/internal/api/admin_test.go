package api

import (
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"

	"vbgw-orchestrator/internal/session"
)

func TestGetActiveSessions_Empty(t *testing.T) {
	store := session.NewMemoryStore(10)
	handler := NewAdminHandler(store)

	req := httptest.NewRequest("GET", "/api/v1/admin/sessions/active", nil)
	w := httptest.NewRecorder()
	handler.GetActiveSessions(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var resp map[string]interface{}
	json.NewDecoder(w.Body).Decode(&resp)
	if resp["count"].(float64) != 0 {
		t.Fatalf("expected count=0, got %v", resp["count"])
	}
}

func TestGetActiveSessions_WithSessions(t *testing.T) {
	ctx := context.Background()
	store := session.NewMemoryStore(10)

	s1 := session.NewSession("n1", "sess-1", "fs-1", "010-1111-2222", "100")
	s2 := session.NewSession("n1", "sess-2", "fs-2", "010-3333-4444", "200")
	store.AddIfUnderCapacity(ctx, s1)
	store.AddIfUnderCapacity(ctx, s2)

	handler := NewAdminHandler(store)
	req := httptest.NewRequest("GET", "/api/v1/admin/sessions/active", nil)
	w := httptest.NewRecorder()
	handler.GetActiveSessions(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var resp map[string]interface{}
	json.NewDecoder(w.Body).Decode(&resp)

	count := int(resp["count"].(float64))
	if count != 2 {
		t.Fatalf("expected count=2, got %d", count)
	}

	data := resp["data"].([]interface{})
	if len(data) != 2 {
		t.Fatalf("expected 2 sessions in data array, got %d", len(data))
	}
}
