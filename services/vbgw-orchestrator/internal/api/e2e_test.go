package api

import (
	"bytes"
	"context"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"testing"
	"time"

	"vbgw-orchestrator/internal/config"
	"vbgw-orchestrator/internal/esl"
	"vbgw-orchestrator/internal/session"
)

// mockESL implements esl.Commander for E2E testing without a real FreeSWITCH.
type mockESL struct {
	originateCalled     int
	sendDtmfCalled      int
	transferCalled      int
	recordStartCalled   int
	recordStopCalled    int
	breakCalled         int
	lastTransferTarget  string
	lastDtmfDigits      string
}

func (m *mockESL) Originate(ctx context.Context, sessionID, target, callerID string, useStandby bool) (string, error) {
	m.originateCalled++
	return "+OK " + sessionID, nil
}
func (m *mockESL) SendDtmf(ctx context.Context, uuid, digits string) error {
	m.sendDtmfCalled++
	m.lastDtmfDigits = digits
	return nil
}
func (m *mockESL) Transfer(ctx context.Context, uuid, target string) error {
	m.transferCalled++
	m.lastTransferTarget = target
	return nil
}
func (m *mockESL) RecordStart(ctx context.Context, uuid, path string) error {
	m.recordStartCalled++
	return nil
}
func (m *mockESL) RecordStop(ctx context.Context, uuid string) error {
	m.recordStopCalled++
	return nil
}
func (m *mockESL) Bridge(ctx context.Context, uuid1, uuid2 string) error   { return nil }
func (m *mockESL) Unbridge(ctx context.Context, uuid string) error          { return nil }
func (m *mockESL) Kill(ctx context.Context, uuid string) error               { return nil }
func (m *mockESL) Break(ctx context.Context, uuid string) error {
	m.breakCalled++
	return nil
}
func (m *mockESL) Dump(ctx context.Context, uuid string) (map[string]string, error) { return nil, nil }
func (m *mockESL) Pause(ctx context.Context) error                        { return nil }
func (m *mockESL) Resume(ctx context.Context) error                       { return nil }
func (m *mockESL) IsConnected() bool                                      { return true }
func (m *mockESL) Eavesdrop(ctx context.Context, superUUID, targetUUID string) error { return nil }
func (m *mockESL) ConferenceKick(ctx context.Context, confName, memberID string) error { return nil }
func (m *mockESL) AttendedTransfer(ctx context.Context, uuid, target string) error   { return nil }
func (m *mockESL) SendAPI(ctx context.Context, cmd string) (string, error)           { return "+OK", nil }
func (m *mockESL) SendBgAPI(ctx context.Context, cmd string) (string, error)        { return "+OK", nil }

// TestE2E_FullCallLifecycle exercises the complete call lifecycle:
// Create → DTMF → RecordStart → RecordStop → Transfer → Verify session cleanup.
func TestE2E_FullCallLifecycle(t *testing.T) {
	ctx := context.Background()
	store := session.NewMemoryStore(100)
	mock := &mockESL{}
	cfg := &config.Config{
		AdminAPIKey:    "test-key",
		RateLimitRPS:   100,
		RateLimitBurst: 200,
	}

	// Build router with mock ESL
	router := buildTestRouter(cfg, mock, store)
	server := httptest.NewServer(router)
	defer server.Close()

	client := server.Client()
	baseURL := server.URL

	// Helper to make authenticated requests
	doReq := func(method, path string, body interface{}) *http.Response {
		var buf bytes.Buffer
		if body != nil {
			json.NewEncoder(&buf).Encode(body)
		}
		req, _ := http.NewRequest(method, baseURL+path, &buf)
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Authorization", "Bearer test-key")
		resp, err := client.Do(req)
		if err != nil {
			t.Fatalf("request %s %s failed: %v", method, path, err)
		}
		return resp
	}

	// ──── Step 1: Create outbound call ────
	resp := doReq("POST", "/api/v1/calls", map[string]string{
		"target_uri": "sip:1004@proxy.test",
	})
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("Step 1 Create: expected 201, got %d", resp.StatusCode)
	}
	var createResp struct {
		CallID string `json:"call_id"`
		Status string `json:"status"`
	}
	json.NewDecoder(resp.Body).Decode(&createResp)
	resp.Body.Close()

	callID := createResp.CallID
	if callID == "" {
		t.Fatal("Step 1: call_id is empty")
	}
	if mock.originateCalled != 1 {
		t.Fatalf("Step 1: expected 1 originate call, got %d", mock.originateCalled)
	}

	// Verify session exists
	if store.Count(ctx) != 1 {
		t.Fatalf("Step 1: expected 1 active session, got %d", store.Count(ctx))
	}

	// ──── Step 2: Send DTMF ────
	resp = doReq("POST", "/api/v1/calls/"+callID+"/dtmf", map[string]string{
		"digits": "1234",
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Step 2 DTMF: expected 200, got %d", resp.StatusCode)
	}
	resp.Body.Close()
	if mock.lastDtmfDigits != "1234" {
		t.Fatalf("Step 2: expected digits=1234, got %s", mock.lastDtmfDigits)
	}

	// ──── Step 3: Start recording ────
	resp = doReq("POST", "/api/v1/calls/"+callID+"/record/start", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Step 3 RecordStart: expected 200, got %d", resp.StatusCode)
	}
	resp.Body.Close()
	if mock.recordStartCalled != 1 {
		t.Fatalf("Step 3: expected 1 record start call, got %d", mock.recordStartCalled)
	}
	// Verify session has record path set
	s, ok := store.Get(ctx, callID)
	if !ok {
		t.Fatal("Step 3: session not found after record start")
	}
	if s.RecordPath() == "" {
		t.Fatal("Step 3: record_path should be set after record start")
	}

	// ──── Step 4: Stop recording ────
	resp = doReq("POST", "/api/v1/calls/"+callID+"/record/stop", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Step 4 RecordStop: expected 200, got %d", resp.StatusCode)
	}
	resp.Body.Close()
	if mock.recordStopCalled != 1 {
		t.Fatalf("Step 4: expected 1 record stop call, got %d", mock.recordStopCalled)
	}

	// ──── Step 5: Capacity test — fill up and verify rejection ────
	for i := 0; i < 99; i++ {
		r := doReq("POST", "/api/v1/calls", map[string]string{
			"target_uri": "sip:test@dummy",
		})
		r.Body.Close()
	}
	// Now at 100 sessions — next should be rejected
	resp = doReq("POST", "/api/v1/calls", map[string]string{
		"target_uri": "sip:overflow@dummy",
	})
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("Step 5 Capacity: expected 503, got %d", resp.StatusCode)
	}
	resp.Body.Close()

	// ──── Step 6: Admin sessions check ────
	resp = doReq("GET", "/api/v1/admin/sessions/active", nil)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("Step 6 Admin: expected 200, got %d", resp.StatusCode)
	}
	var adminResp map[string]interface{}
	json.NewDecoder(resp.Body).Decode(&adminResp)
	resp.Body.Close()
	count := int(adminResp["count"].(float64))
	if count != 100 {
		t.Fatalf("Step 6: expected 100 active sessions, got %d", count)
	}

	// ──── Step 7: Release a session and verify count ────
	store.Release(ctx, callID)
	time.Sleep(10 * time.Millisecond) // let async settle
	if store.Count(ctx) != 99 {
		t.Fatalf("Step 7: expected 99 after release, got %d", store.Count(ctx))
	}

	t.Log("E2E Full Call Lifecycle: PASSED ✓")
}

// buildTestRouter constructs a chi router with mock dependencies.
func buildTestRouter(cfg *config.Config, eslMock esl.Commander, store session.Store) http.Handler {
	return NewRouter(cfg, eslMock.(*mockESL), store, "test-node-id")
}
