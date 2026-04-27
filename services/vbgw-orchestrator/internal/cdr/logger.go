/**
 * @file logger.go
 * @description CDR (Call Detail Record) JSON 로거
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | CHANNEL_HANGUP_COMPLETE → JSON CDR
 * ─────────────────────────────────────────
 */

package cdr

import (
	"log/slog"
	"time"

	"vbgw-orchestrator/internal/metrics"
	"vbgw-orchestrator/internal/session"
)

// Record represents a Call Detail Record.
type Record struct {
	SessionID  string  `json:"session_id"`
	FSUUID     string  `json:"fs_uuid"`
	CallerID   string  `json:"caller_id"`
	DestNum    string  `json:"dest_number"`
	StartTime  string  `json:"start_time"`
	AnswerTime string  `json:"answer_time,omitempty"`
	EndTime    string  `json:"end_time"`
	DurationS  float64 `json:"duration_seconds"`
	HangupCode string  `json:"hangup_code,omitempty"`
	BridgedWith string `json:"bridged_with,omitempty"` // R-05: 브릿지 상대방 세션 ID
}

// LogHangup writes a CDR entry for a completed call.
func LogHangup(s *session.SessionState, hangupCode string) {
	now := time.Now()
	s.SetHangupAt(now)

	duration := now.Sub(s.CreatedAt).Seconds()
	metrics.SessionDuration.Observe(duration)

	cdr := Record{
		SessionID:   s.SessionID,
		FSUUID:      s.FSUUID,
		CallerID:    s.CallerID,
		DestNum:     s.DestNum,
		StartTime:   s.CreatedAt.Format(time.RFC3339),
		EndTime:     now.Format(time.RFC3339),
		DurationS:   duration,
		HangupCode:  hangupCode,
		BridgedWith: s.BridgedWith(), // R-05: 브릿지 상대방 기록
	}

	if !s.AnsweredAt.IsZero() {
		cdr.AnswerTime = s.AnsweredAt.Format(time.RFC3339)
	}

	slog.Info("CDR",
		"session_id", cdr.SessionID,
		"fs_uuid", cdr.FSUUID,
		"caller_id", cdr.CallerID,
		"dest_number", cdr.DestNum,
		"start_time", cdr.StartTime,
		"answer_time", cdr.AnswerTime,
		"end_time", cdr.EndTime,
		"duration_seconds", cdr.DurationS,
		"hangup_code", cdr.HangupCode,
		"bridged_with", cdr.BridgedWith,
	)
}
