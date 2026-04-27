package api

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"testing"

	"vbgw-orchestrator/internal/session"

	"github.com/go-chi/chi/v5"
)

func newStatsTestRouter(h *StatsHandler) *chi.Mux {
	r := chi.NewRouter()
	r.Get("/api/v1/calls/{id}/stats", h.GetStats)
	return r
}

func TestGetStats_Success(t *testing.T) {
	sessions := session.NewManager(100)
	s := session.NewSession("call-1", "fs-1", "010", "1001")
	sessions.AddIfUnderCapacity(s)

	h := &StatsHandler{
		ESL: &mockESL{
			dumpResp: map[string]string{
				"channel_state":                "CS_EXECUTE",
				"read_codec":                   "PCMU",
				"write_codec":                  "PCMU",
				"rtp_audio_recv_pt":            "0",
				"rtp_audio_lost_pt":            "0",
				"rtp_audio_jitter_packet_count": "2",
			},
		},
		Sessions: sessions,
	}
	router := newStatsTestRouter(h)

	req := httptest.NewRequest("GET", "/api/v1/calls/call-1/stats", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusOK {
		t.Fatalf("expected 200, got %d", w.Code)
	}

	var stats callStats
	json.NewDecoder(w.Body).Decode(&stats)

	if stats.ReadCodec != "PCMU" {
		t.Fatalf("expected PCMU, got %s", stats.ReadCodec)
	}
	if stats.SessionState != "CS_EXECUTE" {
		t.Fatalf("expected CS_EXECUTE, got %s", stats.SessionState)
	}
	if stats.CallID != "call-1" {
		t.Fatalf("expected call-1, got %s", stats.CallID)
	}
}

func TestGetStats_SessionNotFound(t *testing.T) {
	h := &StatsHandler{
		ESL:      &mockESL{},
		Sessions: session.NewManager(100),
	}
	router := newStatsTestRouter(h)

	req := httptest.NewRequest("GET", "/api/v1/calls/nonexistent/stats", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusNotFound {
		t.Fatalf("expected 404, got %d", w.Code)
	}
}

func TestGetStats_DumpFailure(t *testing.T) {
	sessions := session.NewManager(100)
	s := session.NewSession("call-1", "fs-1", "010", "1001")
	sessions.AddIfUnderCapacity(s)

	h := &StatsHandler{
		ESL: &mockESL{
			dumpErr: fmt.Errorf("-ERR No Such Channel!"),
		},
		Sessions: sessions,
	}
	router := newStatsTestRouter(h)

	req := httptest.NewRequest("GET", "/api/v1/calls/call-1/stats", nil)
	w := httptest.NewRecorder()
	router.ServeHTTP(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}
}
