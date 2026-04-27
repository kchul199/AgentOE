/**
 * @file constants.go
 * @description VAD 공통 상수 및 유틸리티 — 빌드 태그와 무관하게 공유
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | [Implementer] | Phase 2 | 빌드 태그 분리용 공통 상수
 * ─────────────────────────────────────────
 */

package vad

import "encoding/binary"

const (
	// vadWindowSamples is the number of 16kHz samples for a 32ms VAD window.
	vadWindowSamples = 512 // 32ms @ 16kHz
)

// bytesToInt16 converts little-endian PCM bytes to int16 samples.
func bytesToInt16(data []byte) []int16 {
	n := len(data) / 2
	samples := make([]int16, n)
	for i := 0; i < n; i++ {
		samples[i] = int16(binary.LittleEndian.Uint16(data[i*2:]))
	}
	return samples
}
