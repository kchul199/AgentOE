package api

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"vbgw-orchestrator/internal/session"
)

// mockESL implements esl.Commander for testing.
type mockESL struct {
	originateErr error
	dumpResp     map[string]string
	dumpErr      error
	dtmfErr      error
	transferErr  error
	bridgeErr    error
	unbridgeErr  error
	recordErr    error
	breakErr     error
	killErr      error
	connected    bool
}

func (m *mockESL) Originate(uuid, target, callerID string, useStandby bool) (string, error) {
	return "job-uuid", m.originateErr
}
func (m *mockESL) SendDtmf(uuid, digits string) error      { return m.dtmfErr }
func (m *mockESL) Transfer(uuid, target string) error       { return m.transferErr }
func (m *mockESL) Bridge(uuidA, uuidB string) error         { return m.bridgeErr }
func (m *mockESL) Unbridge(uuid string) error               { return m.unbridgeErr }
func (m *mockESL) RecordStart(uuid, path string) error      { return m.recordErr }
func (m *mockESL) RecordStop(uuid string) error             { return m.recordErr }
func (m *mockESL) Break(uuid string) error                  { return m.breakErr }
func (m *mockESL) Kill(uuid string) error                   { return m.killErr }
func (m *mockESL) Dump(uuid string) (map[string]string, error) { return m.dumpResp, m.dumpErr }
func (m *mockESL) Pause() error                             { return nil }
func (m *mockESL) Resume() error                            { return nil }
func (m *mockESL) IsConnected() bool                        { return m.connected }
func (m *mockESL) Eavesdrop(supervisorUUID, targetUUID string) error { return nil }
func (m *mockESL) ConferenceKick(confName, memberID string) error    { return nil }
func (m *mockESL) AttendedTransfer(uuid, target string) error        { return m.transferErr }

func TestCreateCall_Success(t *testing.T) {
	sessions := session.NewManager(100)
	handler := &CallsHandler{
		ESL:      &mockESL{connected: true},
		Sessions: sessions,
	}

	body := `{"target_uri":"1001@pbx"}`
	req := httptest.NewRequest("POST", "/api/v1/calls", strings.NewReader(body))
	w := httptest.NewRecorder()

	handler.CreateCall(w, req)

	if w.Code != http.StatusCreated {
		t.Fatalf("expected 201, got %d: %s", w.Code, w.Body.String())
	}

	var resp createCallResponse
	json.NewDecoder(w.Body).Decode(&resp)
	if resp.Status != "initiating" {
		t.Fatalf("expected status=initiating, got %s", resp.Status)
	}
	if resp.CallID == "" {
		t.Fatal("expected non-empty call_id")
	}
}

func TestCreateCall_EmptyTarget(t *testing.T) {
	handler := &CallsHandler{
		ESL:      &mockESL{},
		Sessions: session.NewManager(100),
	}

	body := `{"target_uri":""}`
	req := httptest.NewRequest("POST", "/api/v1/calls", strings.NewReader(body))
	w := httptest.NewRecorder()

	handler.CreateCall(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestCreateCall_CapacityExceeded(t *testing.T) {
	sessions := session.NewManager(1) // Max 1 session
	// Fill capacity
	s := session.NewSession("existing", "fs-1", "010", "1001")
	sessions.AddIfUnderCapacity(s)

	handler := &CallsHandler{
		ESL:      &mockESL{connected: true},
		Sessions: sessions,
	}

	body := `{"target_uri":"1002@pbx"}`
	req := httptest.NewRequest("POST", "/api/v1/calls", strings.NewReader(body))
	w := httptest.NewRecorder()

	handler.CreateCall(w, req)

	if w.Code != http.StatusServiceUnavailable {
		t.Fatalf("expected 503, got %d", w.Code)
	}
}

func TestCreateCall_InvalidJSON(t *testing.T) {
	handler := &CallsHandler{
		ESL:      &mockESL{},
		Sessions: session.NewManager(100),
	}

	req := httptest.NewRequest("POST", "/api/v1/calls", strings.NewReader("{invalid"))
	w := httptest.NewRecorder()

	handler.CreateCall(w, req)

	if w.Code != http.StatusBadRequest {
		t.Fatalf("expected 400, got %d", w.Code)
	}
}

func TestCreateCall_ESLOriginateFailure(t *testing.T) {
	handler := &CallsHandler{
		ESL:      &mockESL{originateErr: http.ErrServerClosed},
		Sessions: session.NewManager(100),
	}

	body := `{"target_uri":"1001@pbx"}`
	req := httptest.NewRequest("POST", "/api/v1/calls", strings.NewReader(body))
	w := httptest.NewRecorder()

	handler.CreateCall(w, req)

	if w.Code != http.StatusInternalServerError {
		t.Fatalf("expected 500, got %d", w.Code)
	}

	// Verify session was cleaned up
	if handler.Sessions.Count() != 0 {
		t.Fatalf("expected 0 sessions after failed originate, got %d", handler.Sessions.Count())
	}
}

func TestMaskURI_ShortURI(t *testing.T) {
	if maskURI("abc") != "****" {
		t.Fatalf("expected '****', got '%s'", maskURI("abc"))
	}
}

func TestMaskURI_LongURI(t *testing.T) {
	result := maskURI("sip:1234@pbx")
	if !strings.HasSuffix(result, "@pbx") {
		t.Fatalf("expected suffix '@pbx', got '%s'", result)
	}
	if !strings.HasPrefix(result, "****") {
		t.Fatalf("expected masked prefix, got '%s'", result)
	}
}
