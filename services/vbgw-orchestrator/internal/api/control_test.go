package api

import (
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"vbgw-orchestrator/internal/session"

	"github.com/go-chi/chi/v5"
)

func newControlTestRouter(h *ControlHandler) *chi.Mux {
	r := chi.NewRouter()
	r.Post("/api/v1/calls/{id}/dtmf", h.SendDtmf)
	r.Post("/api/v1/calls/{id}/transfer", h.Transfer)
	r.Post("/api/v1/calls/{id}/record/start", h.RecordStart)
	r.Post("/api/v1/calls/{id}/record/stop", h.RecordStop)
	r.Post("/api/v1/calls/bridge", h.BridgeCalls)
	r.Post("/api/v1/calls/unbridge", h.UnbridgeCalls)
	r.Post("/internal/barge-in/{uuid}", h.BargeIn)
	return r
}

func TestSendDtmf_Success(t *testing.T) {
	sessions := session.NewManager(100)
	s := session.NewSession("call-1", "fs-1", "010", "1001")
	sessions.AddIfUnderCapacity(s)

	bridgeSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer bridgeSrv.Close()

	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   sessions,
		BridgeURL:  bridgeSrv.URL,
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	body := `{"digits":"123#"}`
	req := httptest.NewRequest("POST", "/api/v1/calls/call-1/dtmf", strings.NewReader(body))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestSendDtmf_SessionNotFound(t *testing.T) {
	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   session.NewManager(100),
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	body := `{"digits":"1"}`
	req := httptest.NewRequest("POST", "/api/v1/calls/nonexistent/dtmf", strings.NewReader(body))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestSendDtmf_InvalidDigits(t *testing.T) {
	sessions := session.NewManager(100)
	s := session.NewSession("call-1", "fs-1", "010", "1001")
	sessions.AddIfUnderCapacity(s)

	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   sessions,
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	body := `{"digits":"invalid!@"}`
	req := httptest.NewRequest("POST", "/api/v1/calls/call-1/dtmf", strings.NewReader(body))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestTransfer_Success(t *testing.T) {
	sessions := session.NewManager(100)
	s := session.NewSession("call-1", "fs-1", "010", "1001")
	sessions.AddIfUnderCapacity(s)

	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   sessions,
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	body := `{"target":"1000@pbx"}`
	req := httptest.NewRequest("POST", "/api/v1/calls/call-1/transfer", strings.NewReader(body))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestRecordStart_Success(t *testing.T) {
	sessions := session.NewManager(100)
	s := session.NewSession("call-1", "fs-1", "010", "1001")
	sessions.AddIfUnderCapacity(s)

	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   sessions,
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	req := httptest.NewRequest("POST", "/api/v1/calls/call-1/record/start", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestBridgeCalls_Success(t *testing.T) {
	sessions := session.NewManager(100)
	sA := session.NewSession("call-A", "fs-A", "010", "1001")
	sB := session.NewSession("call-B", "fs-B", "020", "1002")
	sessions.AddIfUnderCapacity(sA)
	sessions.AddIfUnderCapacity(sB)

	bridgeSrv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	defer bridgeSrv.Close()

	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   sessions,
		BridgeURL:  bridgeSrv.URL,
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	body := `{"call_id_1":"call-A","call_id_2":"call-B"}`
	req := httptest.NewRequest("POST", "/api/v1/calls/bridge", strings.NewReader(body))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestBridgeCalls_SessionNotFound(t *testing.T) {
	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   session.NewManager(100),
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	body := `{"call_id_1":"none-A","call_id_2":"none-B"}`
	req := httptest.NewRequest("POST", "/api/v1/calls/bridge", strings.NewReader(body))
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestBargeIn_Success(t *testing.T) {
	h := &ControlHandler{
		ESL:        &mockESL{},
		Sessions:   session.NewManager(100),
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	req := httptest.NewRequest("POST", "/internal/barge-in/fs-uuid-123", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d: %s", w.Code, w.Body.String())
	}
}

func TestBargeIn_ESLFailure(t *testing.T) {
	h := &ControlHandler{
		ESL:        &mockESL{breakErr: fmt.Errorf("ESL timeout")},
		Sessions:   session.NewManager(100),
		httpClient: &http.Client{Timeout: 2 * time.Second},
	}
	router := newControlTestRouter(h)

	req := httptest.NewRequest("POST", "/internal/barge-in/fs-uuid-123", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}
