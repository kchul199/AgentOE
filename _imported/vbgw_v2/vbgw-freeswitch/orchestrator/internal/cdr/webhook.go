/**
 * @file webhook.go
 * @description 백오피스 연동을 위한 CDR Webhook 비동기 전송 모듈 (HMAC 지원)
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-18 | 최초 생성 | Fire-and-Forget CDR Webhook
 * v1.1.0 | 2026-04-19 | C-4 | 전송 실패 시 로컬 파일 백업 큐잉 추가
 * ─────────────────────────────────────────
 */

package cdr

import (
	"bytes"
	"crypto/hmac"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"sync"
	"time"

	"vbgw-orchestrator/internal/config"
	"vbgw-orchestrator/internal/session"
)

type WebhookPayload struct {
	SessionID string `json:"session_id"`
	NodeID    string `json:"node_id"`
	FSUUID    string `json:"fs_uuid"`
	CallerID  string `json:"caller_id"`
	DestNum   string `json:"dest_num"`
	Duration  int    `json:"duration_sec"`
	Cause     string `json:"hangup_cause"`
	Timestamp string `json:"timestamp"`
}

var (
	httpCli = &http.Client{Timeout: 5 * time.Second}

	// C-4 FIX: 백업 파일 쓰기를 직렬화하여 동시 append 충돌 방지
	backupMu   sync.Mutex
	backupDir  = "/var/log/vbgw"
	backupFile = "cdr_failed.jsonl"
)

// SendAsync pushes the Call Detail Record to an external CRM.
// C-4 FIX: 전송 실패 시 로컬 JSONL 파일에 백업하여 CDR 유실을 방지합니다.
func SendAsync(cfg *config.Config, s *session.SessionState, cause string) {
	if cfg.CDRWebhookURL == "" {
		return
	}

	// Calculate duration in seconds
	dur := 0
	if !s.AnsweredAt.IsZero() {
		hangup := s.HangupAt
		if hangup.IsZero() {
			hangup = time.Now()
		}
		dur = int(hangup.Sub(s.AnsweredAt).Seconds())
	}

	payload := WebhookPayload{
		SessionID: s.SessionID,
		NodeID:    s.NodeID,
		FSUUID:    s.FSUUID,
		CallerID:  s.CallerID,
		DestNum:   s.DestNum,
		Duration:  dur,
		Cause:     cause,
		Timestamp: time.Now().UTC().Format(time.RFC3339),
	}

	go func() {
		body, _ := json.Marshal(payload)
		req, err := http.NewRequest("POST", cfg.CDRWebhookURL, bytes.NewBuffer(body))
		if err != nil {
			slog.Error("Failed to create CDR webhook request", "err", err)
			backupToFile(body)
			return
		}

		req.Header.Set("Content-Type", "application/json")

		// Apply HMAC-SHA256 Signature for authenticity validation
		if cfg.CDRWebhookSecret != "" {
			mac := hmac.New(sha256.New, []byte(cfg.CDRWebhookSecret))
			mac.Write(body)
			signature := hex.EncodeToString(mac.Sum(nil))
			req.Header.Set("X-VBGW-Signature", signature)
		}

		resp, err := httpCli.Do(req)
		if err != nil {
			slog.Warn("CDR Webhook delivery failed — backing up locally",
				"url", cfg.CDRWebhookURL, "err", err, "session_id", payload.SessionID)
			backupToFile(body)
			return
		}
		defer resp.Body.Close()

		if resp.StatusCode >= 400 {
			slog.Warn("CDR Webhook rejected by server — backing up locally",
				"status", resp.StatusCode, "session_id", payload.SessionID)
			backupToFile(body)
		} else {
			slog.Debug("CDR Webhook delivered successfully", "session_id", payload.SessionID)
		}
	}()
}

// backupToFile appends a failed CDR payload to a local JSONL file.
// This ensures zero CDR loss even when the external CRM is unavailable.
// A separate cron job or replay script can later re-send these records.
func backupToFile(body []byte) {
	backupMu.Lock()
	defer backupMu.Unlock()

	if err := os.MkdirAll(backupDir, 0o755); err != nil {
		slog.Error("Failed to create CDR backup directory", "dir", backupDir, "err", err)
		return
	}

	path := filepath.Join(backupDir, backupFile)
	f, err := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if err != nil {
		slog.Error("Failed to open CDR backup file", "path", path, "err", err)
		return
	}
	defer f.Close()

	// Write as newline-delimited JSON (JSONL)
	f.Write(body)
	f.Write([]byte("\n"))

	slog.Info("CDR backed up to local file", "path", path)
}
