/**
 * @file admin.go
 * @description 백오피스 연동을 위한 전역 관제/모니터링 API 엔드포인트
 */

package api

import (
	"encoding/json"
	"net/http"

	"vbgw-orchestrator/internal/session"
)

type AdminHandler struct {
	sessionMgr session.Store
}

func NewAdminHandler(sessionMgr session.Store) *AdminHandler {
	return &AdminHandler{
		sessionMgr: sessionMgr,
	}
}

// GetActiveSessions fetches a comprehensive list of all active sessions across the cluster.
// For purely local memory stores, it fetches local.
// For Redis, it iterates the keyspace (this should be used sparingly by Dashboards, e.g. polling every 5s).
func (h *AdminHandler) GetActiveSessions(w http.ResponseWriter, r *http.Request) {
	ctx := r.Context()
	
	// Create an aggregation slice
	var activeSessions []map[string]interface{}

	// Iterate through local sessions (Since RedisStore isn't exposing a Scan() method natively yet,
	// we will proxy through ForEachLocal for the MemoryStore, and for Redis deployments this will fetch
	// the locally homed sessions per orchestrator pod. In a fully distributed Service Mesh,
	// dashboards can aggregate /admin/sessions/active across all orchestrator pods).
	h.sessionMgr.ForEachLocal(func(s *session.SessionState) {
		activeSessions = append(activeSessions, map[string]interface{}{
			"session_id":  s.SessionID,
			"node_id":     s.NodeID,
			"fs_uuid":     s.FSUUID,
			"caller_id":   s.CallerID,
			"dest_num":    s.DestNum,
			"is_ai_paused": s.IsAIPaused(),
			"created_at":  s.CreatedAt,
		})
	})

	// Wrap in a response structure
	payload := map[string]interface{}{
		"status": "success",
		"count":  len(activeSessions),
		"data":   activeSessions,
	}

	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(http.StatusOK)
	if err := json.NewEncoder(w).Encode(payload); err != nil {
		http.Error(w, err.Error(), http.StatusInternalServerError)
	}
}
