/**
 * @file stats.go
 * @description GET /api/v1/calls/{id}/stats — RTP 통계 조회 (uuid_dump 파싱)
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | uuid_dump 기반 RTP 통계
 * ─────────────────────────────────────────
 */

package api

import (
	"encoding/json"
	"net/http"

	"vbgw-orchestrator/internal/esl"
	"vbgw-orchestrator/internal/session"

	"github.com/go-chi/chi/v5"
)

// StatsHandler handles call statistics requests.
type StatsHandler struct {
	ESL      esl.Commander
	Sessions session.Store
}

type callStats struct {
	CallID       string `json:"call_id"`
	FSUUID       string `json:"fs_uuid"`
	CallerID     string `json:"caller_id"`
	DestNum      string `json:"dest_number"`
	RTPPackets   string `json:"rtp_packets,omitempty"`
	RTPLoss      string `json:"rtp_loss,omitempty"`
	RTPJitter    string `json:"rtp_jitter,omitempty"`
	ReadCodec    string `json:"read_codec,omitempty"`
	WriteCodec   string `json:"write_codec,omitempty"`
	SessionState string `json:"session_state,omitempty"`
}

// GetStats handles GET /api/v1/calls/{id}/stats.
func (h *StatsHandler) GetStats(w http.ResponseWriter, r *http.Request) {
	callID := chi.URLParam(r, "id")
	ctx := r.Context()
	s, ok := h.Sessions.Get(ctx, callID)
	if !ok {
		http.Error(w, `{"error":"session not found"}`, http.StatusNotFound)
		return
	}

	dump, err := h.ESL.Dump(ctx, s.FSUUID)
	if err != nil {
		http.Error(w, `{"error":"uuid_dump failed"}`, http.StatusInternalServerError)
		return
	}

	stats := callStats{
		CallID:       s.SessionID,
		FSUUID:       s.FSUUID,
		CallerID:     s.CallerID,
		DestNum:      s.DestNum,
		RTPPackets:   dump["rtp_audio_recv_pt"],
		RTPLoss:      dump["rtp_audio_lost_pt"],
		RTPJitter:    dump["rtp_audio_jitter_packet_count"],
		ReadCodec:    dump["read_codec"],
		WriteCodec:   dump["write_codec"],
		SessionState: dump["channel_state"],
	}

	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(stats)
}
