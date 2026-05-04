package ws

import (
	"testing"
)

func TestSession_SetAIPaused(t *testing.T) {
	s := &Session{}

	if s.aiPaused.Load() {
		t.Fatal("expected aiPaused=false initially")
	}

	s.SetAIPaused(true)
	if !s.aiPaused.Load() {
		t.Fatal("expected aiPaused=true after set")
	}

	s.SetAIPaused(false)
	if s.aiPaused.Load() {
		t.Fatal("expected aiPaused=false after unset")
	}
}

func TestSession_PCMChannel_NonBlockingDrop(t *testing.T) {
	pcmCh := make(chan []byte, 2)

	// Fill the channel
	pcmCh <- []byte("frame1")
	pcmCh <- []byte("frame2")

	// Non-blocking send should not panic or block
	select {
	case pcmCh <- []byte("frame3"):
		t.Fatal("expected channel to be full")
	default:
		// Expected: channel is full, frame dropped
	}

	// Verify existing frames are intact
	f1 := <-pcmCh
	if string(f1) != "frame1" {
		t.Fatalf("expected frame1, got %s", string(f1))
	}
}

func TestSession_TTSChannel_NonBlockingDrop(t *testing.T) {
	ttsCh := make(chan []byte, 2)

	// Fill the channel
	ttsCh <- []byte("tts1")
	ttsCh <- []byte("tts2")

	// Non-blocking send should drop new frame
	select {
	case ttsCh <- []byte("tts3"):
		t.Fatal("expected channel to be full")
	default:
		// Expected: channel is full, frame dropped
	}

	// Verify existing frames are intact
	f1 := <-ttsCh
	if string(f1) != "tts1" {
		t.Fatalf("expected tts1, got %s", string(f1))
	}
}

func TestSession_ChannelCapacity(t *testing.T) {
	if pcmChCap != 200 {
		t.Fatalf("expected pcmChCap=200, got %d", pcmChCap)
	}
	if ttsChCap != 200 {
		t.Fatalf("expected ttsChCap=200, got %d", ttsChCap)
	}
}
