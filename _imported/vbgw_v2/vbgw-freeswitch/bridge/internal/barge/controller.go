/**
 * @file controller.go
 * @description Barge-in 컨트롤러 — clear_buffer → TTS 드레인 + HTTP POST → Orchestrator
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | 2단계 barge-in (드레인 + ESL break)
 * ─────────────────────────────────────────
 */

package barge

import (
	"context"
	"fmt"
	"log/slog"
	"net/http"
	"time"

	"vbgw-bridge/internal/tts"
)

// Controller manages barge-in flow from AI clear_buffer to FS uuid_break.
type Controller struct {
	orchestratorURL string
	httpClient      *http.Client
}

// NewController creates a barge-in controller.
func NewController(orchestratorURL string) *Controller {
	return &Controller{
		orchestratorURL: orchestratorURL,
		httpClient:      &http.Client{Timeout: 3 * time.Second},
	}
}

// HandleClearBuffer processes a barge-in event:
// 1. Drain TTS channel (discard pending audio)
// 2. Notify Orchestrator → ESL uuid_break
func (c *Controller) HandleClearBuffer(ctx context.Context, uuid string, ttsCh chan []byte) {
	start := time.Now()

	// Step 1: Drain TTS buffer
	drained := tts.Drain(ttsCh)
	slog.Info("TTS buffer drained", "uuid", uuid, "frames_discarded", drained)

	// Step 2: Notify Orchestrator for uuid_break
	url := fmt.Sprintf("%s/internal/barge-in/%s", c.orchestratorURL, uuid)
	req, err := http.NewRequestWithContext(ctx, "POST", url, nil)
	if err != nil {
		slog.Error("Barge-in request creation failed", "uuid", uuid, "err", err)
		return
	}

	resp, err := c.httpClient.Do(req)
	if err != nil {
		slog.Error("Barge-in notification failed", "uuid", uuid, "err", err)
		return
	}
	defer resp.Body.Close()

	elapsed := time.Since(start)
	slog.Info("Barge-in executed",
		"uuid", uuid,
		"status", resp.StatusCode,
		"elapsed_ms", elapsed.Milliseconds(),
		"frames_discarded", drained,
	)
}
