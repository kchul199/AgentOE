package session

import (
	"context"
	"sync"
	"sync/atomic"
	"time"
)

// MemoryStore provides an in-memory implementation of the session Store.
// Used primarily for unit tests and local fuzzing without a Redis dependency.
type MemoryStore struct {
	maxSessions int64
	activeCount int64
	sessions    sync.Map // string -> *SessionState
	uuidIndex   sync.Map // string -> string
}

func NewMemoryStore(max int64) *MemoryStore {
	return &MemoryStore{
		maxSessions: max,
	}
}

func (m *MemoryStore) TryAcquire(ctx context.Context) bool {
	for {
		current := atomic.LoadInt64(&m.activeCount)
		if current >= m.maxSessions {
			return false
		}
		if atomic.CompareAndSwapInt64(&m.activeCount, current, current+1) {
			return true
		}
	}
}

func (m *MemoryStore) AddIfUnderCapacity(ctx context.Context, s *SessionState) bool {
	if !m.TryAcquire(ctx) {
		return false
	}
	m.sessions.Store(s.SessionID, s)
	if s.FSUUID != "" {
		m.uuidIndex.Store(s.FSUUID, s.SessionID)
	}
	return true
}

func (m *MemoryStore) Get(ctx context.Context, sessionID string) (*SessionState, bool) {
	val, ok := m.sessions.Load(sessionID)
	if !ok {
		return nil, false
	}
	return val.(*SessionState), true
}

func (m *MemoryStore) GetByFSUUID(ctx context.Context, fsUUID string) (*SessionState, bool) {
	sidVal, ok := m.uuidIndex.Load(fsUUID)
	if !ok {
		return nil, false
	}
	return m.Get(ctx, sidVal.(string))
}

func (m *MemoryStore) Release(ctx context.Context, sessionID string) {
	val, loaded := m.sessions.LoadAndDelete(sessionID)
	if loaded {
		atomic.AddInt64(&m.activeCount, -1)
		s := val.(*SessionState)
		if s.FSUUID != "" {
			m.uuidIndex.Delete(s.FSUUID)
		}
		if s.IvrEventCh != nil {
			close(s.IvrEventCh)
			s.IvrEventCh = nil
		}
		s.Cancel()
	}
}

func (m *MemoryStore) Count(ctx context.Context) int64 {
	return atomic.LoadInt64(&m.activeCount)
}

func (m *MemoryStore) WaitAllDrained(ctx context.Context, killFn func(fsUUID string)) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	for {
		if atomic.LoadInt64(&m.activeCount) == 0 {
			return
		}
		select {
		case <-ctx.Done():
			m.ForEachLocal(func(s *SessionState) {
				if killFn != nil && s.FSUUID != "" {
					killFn(s.FSUUID)
				}
				m.Release(ctx, s.SessionID)
			})
			return
		case <-ticker.C:
		}
	}
}

func (m *MemoryStore) ForEachLocal(fn func(s *SessionState)) {
	m.sessions.Range(func(_, value interface{}) bool {
		fn(value.(*SessionState))
		return true
	})
}

func (m *MemoryStore) PublishCommand(ctx context.Context, targetNodeID, sessionID, action string, payload interface{}) error {
	// For memory store (single node), we can synchronously execute via a mock or ignore.
	return nil
}

func (m *MemoryStore) SubscribeCommands(ctx context.Context, handler func(msg CommandMsg)) {
	// No-op for in-memory single node.
	<-ctx.Done()
}
