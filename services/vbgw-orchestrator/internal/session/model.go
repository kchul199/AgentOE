/**
 * @file model.go
 * @description 세션 상태 모델 — 콜 세션 데이터 구조
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | SessionState 정의
 * v1.0.1 | 2026-04-07 | [Implementer] | 코드리뷰 C-2 | 뮤텍스 추가 (race condition 수정)
 * ─────────────────────────────────────────
 */

package session

import (
	"context"
	"sync"
	"time"
)

// SessionState holds the runtime state for a single call session.
type SessionState struct {
	mu sync.RWMutex

	// Node binding (which orchestrator instance owns this session locally)
	NodeID string `json:"node_id"`

	// IDs (immutable after creation — no lock needed for reads)
	SessionID string `json:"session_id"`
	FSUUID    string `json:"fs_uuid"`
	CallerID  string `json:"caller_id"`
	DestNum   string `json:"dest_num"`

	// Timing
	CreatedAt  time.Time `json:"created_at"`
	AnsweredAt time.Time `json:"answered_at"`
	HangupAt   time.Time `json:"hangup_at"`

	// Mutable state (must use accessors)
	aiPaused    bool   `json:"-"`
	AiPausedExport bool `json:"ai_paused"` // Exported for JSON Unmarshal only

	recordPath  string `json:"-"`
	RecordPathExport string `json:"record_path"`

	bridgedWith string `json:"-"`
	BridgedWithExport string `json:"bridged_with"`

	// Local context/channels (Not serialized to Redis)
	IvrEventCh chan any             `json:"-"`
	Ctx        context.Context      `json:"-"`
	Cancel     context.CancelFunc   `json:"-"`
}

// NewSession creates a new SessionState with the given IDs.
func NewSession(nodeID, sessionID, fsUUID, callerID, destNum string) *SessionState {
	ctx, cancel := context.WithCancel(context.Background())
	return &SessionState{
		NodeID:    nodeID,
		SessionID: sessionID,
		FSUUID:    fsUUID,
		CallerID:  callerID,
		DestNum:   destNum,
		CreatedAt: time.Now(),
		Ctx:       ctx,
		Cancel:    cancel,
	}
}

func (s *SessionState) IsAIPaused() bool {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.aiPaused
}

func (s *SessionState) SetAIPaused(v bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.aiPaused = v
}

func (s *SessionState) RecordPath() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.recordPath
}

func (s *SessionState) SetRecordPath(v string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.recordPath = v
}

func (s *SessionState) BridgedWith() string {
	s.mu.RLock()
	defer s.mu.RUnlock()
	return s.bridgedWith
}

func (s *SessionState) SetBridgedWith(v string) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.bridgedWith = v
}

func (s *SessionState) SetAnsweredAt(t time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.AnsweredAt = t
}

func (s *SessionState) SetHangupAt(t time.Time) {
	s.mu.Lock()
	defer s.mu.Unlock()
	s.HangupAt = t
}
