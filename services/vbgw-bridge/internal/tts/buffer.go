/**
 * @file buffer.go
 * @description TTS 버퍼 — buffered channel (cap=200), oldest-drop 정책
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | 최초 생성 | 5초 분량 TTS 링버퍼
 * ─────────────────────────────────────────
 */

package tts

import (
	"log/slog"
)

const (
	// DefaultCapacity is 200 frames (20ms × 200 = 4 seconds of audio).
	DefaultCapacity = 200
)

// Enqueue sends a TTS frame to the channel, dropping the oldest if full.
func Enqueue(ch chan []byte, frame []byte) {
	select {
	case ch <- frame:
	default:
		// Channel full — drop oldest
		select {
		case <-ch:
			slog.Warn("TTS buffer overflow, dropped oldest frame")
		default:
		}
		ch <- frame
	}
}

// Drain discards all pending frames from the TTS channel.
// Returns the number of frames discarded.
func Drain(ch chan []byte) int {
	drained := 0
	for {
		select {
		case <-ch:
			drained++
		default:
			return drained
		}
	}
}
