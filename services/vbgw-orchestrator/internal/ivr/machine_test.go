package ivr

import (
	"context"
	"sync"
	"testing"
	"time"
)

// callbackTracker uses mutex for thread-safe access.
type callbackTracker struct {
	mu              sync.Mutex
	menuCount       int
	aiChatCount     int
	transferCount   int
	disconnectCount int
	forwardedDigits []string
	lastState       chan State // receives state after each callback
}

func newTracker() *callbackTracker {
	return &callbackTracker{
		lastState: make(chan State, 32),
	}
}

func (ct *callbackTracker) getMenuCount() int {
	ct.mu.Lock(); defer ct.mu.Unlock(); return ct.menuCount
}
func (ct *callbackTracker) getTransferCount() int {
	ct.mu.Lock(); defer ct.mu.Unlock(); return ct.transferCount
}
func (ct *callbackTracker) getForwardedDigits() []string {
	ct.mu.Lock(); defer ct.mu.Unlock()
	cp := make([]string, len(ct.forwardedDigits))
	copy(cp, ct.forwardedDigits)
	return cp
}

// waitState waits for a state notification from the callback or times out.
func (ct *callbackTracker) waitState(timeout time.Duration) (State, bool) {
	select {
	case s := <-ct.lastState:
		return s, true
	case <-time.After(timeout):
		return Idle, false
	}
}

func newTestMachine() (*Machine, *callbackTracker) {
	tracker := newTracker()
	m := NewMachine("test-session", Callbacks{
		OnRepeatMenu: func() {
			tracker.mu.Lock()
			tracker.menuCount++
			tracker.mu.Unlock()
			tracker.lastState <- Menu
		},
		OnEnterAiChat: func() {
			tracker.mu.Lock()
			tracker.aiChatCount++
			tracker.mu.Unlock()
			tracker.lastState <- AiChat
		},
		OnTransfer: func() {
			tracker.mu.Lock()
			tracker.transferCount++
			tracker.mu.Unlock()
			tracker.lastState <- Transfer
		},
		OnDisconnect: func() {
			tracker.mu.Lock()
			tracker.disconnectCount++
			tracker.mu.Unlock()
			tracker.lastState <- Disconnect
		},
		OnForwardDtmf: func(d string) {
			tracker.mu.Lock()
			tracker.forwardedDigits = append(tracker.forwardedDigits, d)
			tracker.mu.Unlock()
			// Forward doesn't change state, send current
			tracker.lastState <- AiChat
		},
	})
	return m, tracker
}

func TestIVR_InitialState(t *testing.T) {
	m, _ := newTestMachine()
	if m.State() != Idle {
		t.Fatalf("expected Idle, got %s", m.State())
	}
}

func TestIVR_ActivateMenu(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	state, ok := tracker.waitState(2 * time.Second)
	if !ok {
		t.Fatal("timeout waiting for ActivateMenu callback")
	}
	if state != Menu {
		t.Fatalf("expected Menu, got %s", state)
	}
}

func TestIVR_MenuToAiChat(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second) // Menu

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "1"}
	state, ok := tracker.waitState(2 * time.Second)
	if !ok {
		t.Fatal("timeout")
	}
	if state != AiChat {
		t.Fatalf("expected AiChat, got %s", state)
	}
}

func TestIVR_MenuToTransfer(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second)

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "0"}
	state, _ := tracker.waitState(2 * time.Second)
	if state != Transfer {
		t.Fatalf("expected Transfer, got %s", state)
	}
}

func TestIVR_MenuToDisconnect(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second)

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "#"}
	state, _ := tracker.waitState(2 * time.Second)
	if state != Disconnect {
		t.Fatalf("expected Disconnect, got %s", state)
	}
}

func TestIVR_MenuRepeat(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second) // menu count = 1

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "*"}
	tracker.waitState(2 * time.Second) // menu count = 2

	if tracker.getMenuCount() != 2 {
		t.Fatalf("expected 2 menu calls, got %d", tracker.getMenuCount())
	}
}

func TestIVR_AiChatForwardDtmf(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second)

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "1"} // → AiChat
	tracker.waitState(2 * time.Second)

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "5"} // → forward
	tracker.waitState(2 * time.Second)

	digits := tracker.getForwardedDigits()
	if len(digits) != 1 || digits[0] != "5" {
		t.Fatalf("expected ['5'], got %v", digits)
	}
}

func TestIVR_AiChatToTransfer(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second)
	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "1"}
	tracker.waitState(2 * time.Second)
	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "0"}
	state, _ := tracker.waitState(2 * time.Second)

	if state != Transfer {
		t.Fatalf("expected Transfer, got %s", state)
	}
}

func TestIVR_AiChatBackToMenu(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second)
	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "1"}
	tracker.waitState(2 * time.Second)
	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "*"}
	state, _ := tracker.waitState(2 * time.Second)

	if state != Menu {
		t.Fatalf("expected Menu, got %s", state)
	}
}

func TestIVR_FullSequence(t *testing.T) {
	m, tracker := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go m.Run(ctx)

	m.EventCh <- IvrEvent{Type: ActivateMenuEvent}
	tracker.waitState(2 * time.Second)

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "1"} // → AiChat
	tracker.waitState(2 * time.Second)

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "2"} // → forward
	tracker.waitState(2 * time.Second)

	m.EventCh <- IvrEvent{Type: DtmfEvent, Digit: "0"} // → Transfer
	state, _ := tracker.waitState(2 * time.Second)

	if state != Transfer {
		t.Fatalf("expected Transfer, got %s", state)
	}
	digits := tracker.getForwardedDigits()
	if len(digits) != 1 || digits[0] != "2" {
		t.Fatalf("expected ['2'], got %v", digits)
	}
	if tracker.getTransferCount() != 1 {
		t.Fatalf("expected 1 transfer, got %d", tracker.getTransferCount())
	}
}

func TestIVR_ContextCancellation(t *testing.T) {
	m, _ := newTestMachine()
	ctx, cancel := context.WithCancel(context.Background())

	done := make(chan struct{})
	go func() {
		m.Run(ctx)
		close(done)
	}()

	cancel()

	select {
	case <-done:
	case <-time.After(1 * time.Second):
		t.Fatal("Run should exit within 1s after context cancellation")
	}
}
