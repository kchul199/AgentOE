package vad

import (
	"encoding/binary"
	"testing"
)

func TestBytesToInt16(t *testing.T) {
	// 3 samples: 100, -200, 32767
	buf := make([]byte, 6)
	binary.LittleEndian.PutUint16(buf[0:], uint16(100))
	// -200 as int16 → two's complement uint16
	var neg200 int16 = -200
	binary.LittleEndian.PutUint16(buf[2:], uint16(neg200))
	binary.LittleEndian.PutUint16(buf[4:], uint16(32767))

	samples := bytesToInt16(buf)

	if len(samples) != 3 {
		t.Fatalf("expected 3 samples, got %d", len(samples))
	}
	if samples[0] != 100 {
		t.Fatalf("expected 100, got %d", samples[0])
	}
	if samples[1] != -200 {
		t.Fatalf("expected -200, got %d", samples[1])
	}
	if samples[2] != 32767 {
		t.Fatalf("expected 32767, got %d", samples[2])
	}
}

func TestBytesToInt16_Empty(t *testing.T) {
	samples := bytesToInt16([]byte{})
	if len(samples) != 0 {
		t.Fatalf("expected 0 samples, got %d", len(samples))
	}
}

func TestBytesToInt16_OddBytes(t *testing.T) {
	// 5 bytes → 2 samples (last byte discarded)
	buf := []byte{0x01, 0x00, 0x02, 0x00, 0xFF}
	samples := bytesToInt16(buf)

	if len(samples) != 2 {
		t.Fatalf("expected 2 samples from 5 bytes, got %d", len(samples))
	}
}

func TestVadWindowSamples_Is512(t *testing.T) {
	// 32ms @ 16kHz = 512 samples
	expected := 16000 * 32 / 1000
	if vadWindowSamples != expected {
		t.Fatalf("vadWindowSamples should be %d (32ms @ 16kHz), got %d", expected, vadWindowSamples)
	}
}

func TestNewEngine_StubMode(t *testing.T) {
	// Non-existent model path → should initialize without panic (stub mode)
	e := NewEngine("/nonexistent/path/silero_vad.onnx")
	if e == nil {
		t.Fatal("expected non-nil engine")
	}
	defer e.Close()
}

func TestProcess_SilenceReturnsFalse(t *testing.T) {
	e := NewEngine("/nonexistent/path/silero_vad.onnx")
	defer e.Close()

	// 512 samples of silence (all zeros) = 1024 bytes
	silence := make([]byte, 1024)
	result := e.Process(silence)

	if result {
		t.Fatal("expected silence to return false (not speaking)")
	}
}

func TestProcess_LoudAudioReturnsTrue(t *testing.T) {
	e := NewEngine("/nonexistent/path/silero_vad.onnx")
	defer e.Close()

	// 512 samples of loud audio (amplitude > 800)
	loud := make([]byte, 1024)
	for i := 0; i < 512; i++ {
		// Write int16 value 5000 (well above 800 threshold)
		binary.LittleEndian.PutUint16(loud[i*2:], uint16(5000))
	}

	result := e.Process(loud)

	if !result {
		t.Fatal("expected loud audio to return true (speaking) in stub mode")
	}
}

func TestProcess_AccumulatesAcrossFrames(t *testing.T) {
	e := NewEngine("/nonexistent/path/silero_vad.onnx")
	defer e.Close()

	// Send 256 samples (half window) → should return false (not enough data)
	half := make([]byte, 512) // 256 samples
	result := e.Process(half)
	if result {
		t.Fatal("expected false with only half window")
	}

	// Send another 256 samples → completes a full window
	result = e.Process(half)
	// Result depends on content (silence → false)
	_ = result // just verify no panic
}
