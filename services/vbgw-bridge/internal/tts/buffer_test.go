package tts

import (
	"testing"
)

func TestEnqueue_NormalSend(t *testing.T) {
	ch := make(chan []byte, 3)

	Enqueue(ch, []byte("frame1"))
	Enqueue(ch, []byte("frame2"))
	Enqueue(ch, []byte("frame3"))

	if len(ch) != 3 {
		t.Fatalf("expected 3 frames in channel, got %d", len(ch))
	}
}

func TestEnqueue_OverflowDropsOldest(t *testing.T) {
	ch := make(chan []byte, 2)

	Enqueue(ch, []byte("frame1"))
	Enqueue(ch, []byte("frame2"))
	Enqueue(ch, []byte("frame3")) // should drop frame1

	if len(ch) != 2 {
		t.Fatalf("expected 2 frames after overflow, got %d", len(ch))
	}

	// frame1 should be dropped, frame2 and frame3 remain
	f1 := <-ch
	f2 := <-ch
	if string(f1) != "frame2" {
		t.Fatalf("expected frame2, got %s", string(f1))
	}
	if string(f2) != "frame3" {
		t.Fatalf("expected frame3, got %s", string(f2))
	}
}

func TestDrain_EmptiesChannel(t *testing.T) {
	ch := make(chan []byte, 10)

	Enqueue(ch, []byte("a"))
	Enqueue(ch, []byte("b"))
	Enqueue(ch, []byte("c"))

	drained := Drain(ch)

	if drained != 3 {
		t.Fatalf("expected 3 drained, got %d", drained)
	}
	if len(ch) != 0 {
		t.Fatalf("expected empty channel after drain, got %d", len(ch))
	}
}

func TestDrain_EmptyChannel(t *testing.T) {
	ch := make(chan []byte, 10)

	drained := Drain(ch)

	if drained != 0 {
		t.Fatalf("expected 0 drained from empty channel, got %d", drained)
	}
}

func TestDrain_AfterOverflow(t *testing.T) {
	ch := make(chan []byte, 2)

	Enqueue(ch, []byte("a"))
	Enqueue(ch, []byte("b"))
	Enqueue(ch, []byte("c")) // overflow, drops "a"

	drained := Drain(ch)

	if drained != 2 {
		t.Fatalf("expected 2 drained, got %d", drained)
	}
}
