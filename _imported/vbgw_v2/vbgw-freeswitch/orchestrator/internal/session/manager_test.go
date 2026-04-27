package session

import (
	"context"
	"sync"
	"testing"
	"time"
)

func TestAddIfUnderCapacity_BasicFlow(t *testing.T) {
	ctx := context.Background()
	m := NewMemoryStore(3)

	s1 := NewSession("node1", "s1", "fs1", "010", "100")
	s2 := NewSession("node1", "s2", "fs2", "011", "101")
	s3 := NewSession("node1", "s3", "fs3", "012", "102")
	s4 := NewSession("node1", "s4", "fs4", "013", "103")

	if !m.AddIfUnderCapacity(ctx, s1) {
		t.Fatal("expected s1 to be added")
	}
	if !m.AddIfUnderCapacity(ctx, s2) {
		t.Fatal("expected s2 to be added")
	}
	if !m.AddIfUnderCapacity(ctx, s3) {
		t.Fatal("expected s3 to be added")
	}
	// s4 should be rejected (capacity=3)
	if m.AddIfUnderCapacity(ctx, s4) {
		t.Fatal("expected s4 to be rejected at capacity")
	}
	if m.Count(ctx) != 3 {
		t.Fatalf("expected count=3, got %d", m.Count(ctx))
	}
}

func TestAddIfUnderCapacity_ConcurrentSafety(t *testing.T) {
	ctx := context.Background()
	capacity := int64(100)
	m := NewMemoryStore(capacity)

	// Spawn 200 goroutines, each trying to add a session
	var wg sync.WaitGroup
	accepted := make(chan string, 200)

	for i := 0; i < 200; i++ {
		wg.Add(1)
		go func(idx int) {
			defer wg.Done()
			sid := "s" + time.Now().Format("150405.000000") + string(rune('A'+idx%26))
			s := NewSession("node1", sid, "fs-"+sid, "", "")
			if m.AddIfUnderCapacity(ctx, s) {
				accepted <- sid
			}
		}(i)
	}

	wg.Wait()
	close(accepted)

	count := 0
	for range accepted {
		count++
	}

	if int64(count) != capacity {
		t.Fatalf("expected exactly %d accepted, got %d", capacity, count)
	}
	if m.Count(ctx) != capacity {
		t.Fatalf("expected count=%d, got %d", capacity, m.Count(ctx))
	}
}

func TestRelease_DecrementsAndCleansUp(t *testing.T) {
	ctx := context.Background()
	m := NewMemoryStore(10)

	s := NewSession("node1", "s1", "fs1", "010", "100")
	m.AddIfUnderCapacity(ctx, s)

	if m.Count(ctx) != 1 {
		t.Fatalf("expected count=1, got %d", m.Count(ctx))
	}

	m.Release(ctx, "s1")

	if m.Count(ctx) != 0 {
		t.Fatalf("expected count=0 after release, got %d", m.Count(ctx))
	}

	// Get should return not found
	if _, ok := m.Get(ctx, "s1"); ok {
		t.Fatal("expected s1 to be gone after release")
	}
	if _, ok := m.GetByFSUUID(ctx, "fs1"); ok {
		t.Fatal("expected fs1 lookup to fail after release")
	}
}

func TestRelease_ClosesIvrEventCh(t *testing.T) {
	ctx := context.Background()
	m := NewMemoryStore(10)
	s := NewSession("node1", "s1", "fs1", "", "")
	ch := make(chan any, 16)
	s.IvrEventCh = ch
	m.AddIfUnderCapacity(ctx, s)

	m.Release(ctx, "s1")

	// After Release, the channel (captured before Release) should be closed.
	// Reading from a closed channel returns zero value immediately.
	select {
	case _, ok := <-ch:
		if ok {
			t.Fatal("expected channel to be closed, but received a value")
		}
		// ok == false means channel is closed — this is correct
	case <-time.After(1 * time.Second):
		t.Fatal("expected closed channel, but read blocked (channel not closed)")
	}
}

func TestRelease_DoubleRelease(t *testing.T) {
	ctx := context.Background()
	m := NewMemoryStore(10)
	s := NewSession("node1", "s1", "fs1", "", "")
	m.AddIfUnderCapacity(ctx, s)

	m.Release(ctx, "s1")
	m.Release(ctx, "s1") // should not panic or go negative

	if m.Count(ctx) != 0 {
		t.Fatalf("expected count=0, got %d", m.Count(ctx))
	}
}

func TestWaitAllDrained_ImmediateReturn(t *testing.T) {
	m := NewMemoryStore(10)
	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()

	// No sessions — should return immediately
	done := make(chan struct{})
	go func() {
		m.WaitAllDrained(ctx, nil)
		close(done)
	}()

	select {
	case <-done:
		// OK
	case <-time.After(1 * time.Second):
		t.Fatal("WaitAllDrained should return immediately with 0 sessions")
	}
}

func TestGetByFSUUID(t *testing.T) {
	ctx := context.Background()
	m := NewMemoryStore(10)
	s := NewSession("node1", "s1", "fs-uuid-123", "010", "100")
	m.AddIfUnderCapacity(ctx, s)

	found, ok := m.GetByFSUUID(ctx, "fs-uuid-123")
	if !ok {
		t.Fatal("expected to find session by FS UUID")
	}
	if found.SessionID != "s1" {
		t.Fatalf("expected session_id=s1, got %s", found.SessionID)
	}

	_, ok = m.GetByFSUUID(ctx, "nonexistent")
	if ok {
		t.Fatal("expected not found for nonexistent UUID")
	}
}

func TestSessionState_Accessors(t *testing.T) {
	s := NewSession("node1", "s1", "fs1", "010", "100")

	// AIPaused
	if s.IsAIPaused() {
		t.Fatal("should not be paused initially")
	}
	s.SetAIPaused(true)
	if !s.IsAIPaused() {
		t.Fatal("should be paused after SetAIPaused(true)")
	}

	// RecordPath
	if s.RecordPath() != "" {
		t.Fatal("should be empty initially")
	}
	s.SetRecordPath("/recordings/test.wav")
	if s.RecordPath() != "/recordings/test.wav" {
		t.Fatalf("expected /recordings/test.wav, got %s", s.RecordPath())
	}

	// BridgedWith
	if s.BridgedWith() != "" {
		t.Fatal("should be empty initially")
	}
	s.SetBridgedWith("s2")
	if s.BridgedWith() != "s2" {
		t.Fatalf("expected s2, got %s", s.BridgedWith())
	}
}

// FuzzMemoryStore_Capacity tests thread safety and limit enforcement under
// pseudo-random interleaved add/release patterns.
func FuzzMemoryStore_Capacity(f *testing.F) {
	// Seed the fuzzer with some simple cases
	f.Add(int64(10), uint8(5), uint8(2))
	f.Add(int64(2), uint8(10), uint8(8))

	f.Fuzz(func(t *testing.T, maxCapacity int64, addOps uint8, relOps uint8) {
		// Cap testing to reasonable max ops per fuzz iteration to prevent timeouts
		if maxCapacity < 1 || maxCapacity > 1000 {
			return
		}
		
		ctx := context.Background()
		m := NewMemoryStore(maxCapacity)
		
		var wg sync.WaitGroup
		
		// Concurrently spawn `addOps` additions and `relOps` releases
		for i := 0; i < int(addOps); i++ {
			wg.Add(1)
			go func(iter int) {
				defer wg.Done()
				sid := string(rune('a' + iter%26)) + time.Now().String()
				m.AddIfUnderCapacity(ctx, NewSession("n1", sid, "fs-"+sid, "", ""))
			}(i)
		}
		
		for i := 0; i < int(relOps); i++ {
			wg.Add(1)
			go func() {
				defer wg.Done()
				// Arbitrary release (mostly nonexistent, but ensures map safety)
				m.Release(ctx, "nonexistent")
			}()
		}
		
		wg.Wait()
		
		// Ultimate validation: count never exceeds capacity
		if m.Count(ctx) > maxCapacity {
			t.Fatalf("Capacity breached! Max: %d, Current: %d", maxCapacity, m.Count(ctx))
		}
		if m.Count(ctx) < 0 {
			t.Fatalf("Capacity dropped below 0! Current: %d", m.Count(ctx))
		}
	})
}
