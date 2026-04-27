/**
 * @file repository.go
 * @description Redis 기반의 세션 저장소 및 Pub/Sub 인터페이스
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | 최초 생성
 * v1.1.0 | 2026-04-18 | Redis TryAcquire, Store interface
 * v1.2.0 | 2026-04-19 | C-1: sync.RWMutex 동시성 보호
 *                      | C-2: Lua Script 원자적 Capacity Check
 *                      | Store interface에 PublishCommand/SubscribeCommands 통합
 * ─────────────────────────────────────────
 */

package session

import (
	"context"
	"encoding/json"
	"errors"
	"log/slog"
	"sync"
	"time"

	"github.com/redis/go-redis/v9"
)

var ErrCapacityExceeded = errors.New("capacity exceeded")

// Store defines the unified interface for session management.
// Merges the previous separate Manager and Store interfaces.
type Store interface {
	TryAcquire(ctx context.Context) bool
	AddIfUnderCapacity(ctx context.Context, s *SessionState) bool
	Get(ctx context.Context, sessionID string) (*SessionState, bool)
	GetByFSUUID(ctx context.Context, fsUUID string) (*SessionState, bool)
	Release(ctx context.Context, sessionID string)
	Count(ctx context.Context) int64
	WaitAllDrained(ctx context.Context, killFn func(fsUUID string))
	ForEachLocal(fn func(s *SessionState))
	PublishCommand(ctx context.Context, targetNodeID, sessionID, action string, payload interface{}) error
	SubscribeCommands(ctx context.Context, handler func(msg CommandMsg))
}

// luaTryAcquire is an atomic Lua script for capacity check + increment.
// It eliminates the INCR→check→DECR race condition (C-2).
// Returns 1 if acquired, 0 if capacity exceeded.
var luaTryAcquire = redis.NewScript(`
local current = redis.call('GET', KEYS[1])
if current == false then current = 0 else current = tonumber(current) end
if current < tonumber(ARGV[1]) then
    redis.call('INCR', KEYS[1])
    return 1
end
return 0
`)

// RedisStore implements the Store interface using Redis.
// C-1 FIX: All localMap/localByUUID access is protected by mu (sync.RWMutex).
type RedisStore struct {
	client   *redis.Client
	nodeID   string
	maxCalls int64

	mu          sync.RWMutex                // C-1: protects localMap and localByUUID
	localMap    map[string]*SessionState    // session_id → *SessionState
	localByUUID map[string]string           // fs_uuid → session_id
}

// NewRedisStore creates a new Redis-backed session store.
func NewRedisStore(addr, pass string, db int, maxCalls int64, nodeID string) (*RedisStore, error) {
	client := redis.NewClient(&redis.Options{
		Addr:     addr,
		Password: pass,
		DB:       db,
	})

	// Check connection
	if err := client.Ping(context.Background()).Err(); err != nil {
		return nil, err
	}

	return &RedisStore{
		client:      client,
		maxCalls:    maxCalls,
		nodeID:      nodeID,
		localMap:    make(map[string]*SessionState),
		localByUUID: make(map[string]string),
	}, nil
}

// TryAcquire atomically checks capacity and increments via Lua script.
// C-2 FIX: Single atomic Redis operation eliminates INCR/DECR race.
func (rs *RedisStore) TryAcquire(ctx context.Context) bool {
	result, err := luaTryAcquire.Run(ctx, rs.client, []string{"vbgw:active_calls"}, rs.maxCalls).Int()
	if err != nil {
		slog.Error("Redis Lua TryAcquire failed", "err", err)
		return false
	}
	return result == 1
}

func (rs *RedisStore) saveToRedis(ctx context.Context, s *SessionState) error {
	// Update Export fields before serialization
	s.mu.RLock()
	s.AiPausedExport = s.aiPaused
	s.RecordPathExport = s.recordPath
	s.BridgedWithExport = s.bridgedWith
	s.mu.RUnlock()

	data, err := json.Marshal(s)
	if err != nil {
		return err
	}

	pipe := rs.client.Pipeline()
	pipe.Set(ctx, "vbgw:session:"+s.SessionID, data, 24*time.Hour)
	if s.FSUUID != "" {
		pipe.Set(ctx, "vbgw:fsuuid:"+s.FSUUID, s.SessionID, 24*time.Hour)
	}
	_, err = pipe.Exec(ctx)
	return err
}

func (rs *RedisStore) AddIfUnderCapacity(ctx context.Context, s *SessionState) bool {
	if !rs.TryAcquire(ctx) {
		return false
	}

	if err := rs.saveToRedis(ctx, s); err != nil {
		slog.Error("Redis save failed", "err", err)
		rs.client.Decr(ctx, "vbgw:active_calls")
		return false
	}

	// C-1 FIX: Protect local map writes with mutex
	rs.mu.Lock()
	rs.localMap[s.SessionID] = s
	if s.FSUUID != "" {
		rs.localByUUID[s.FSUUID] = s.SessionID
	}
	rs.mu.Unlock()
	return true
}

// Get finds session data from local map if present, else from Redis.
func (rs *RedisStore) Get(ctx context.Context, sessionID string) (*SessionState, bool) {
	// C-1 FIX: Protect local map reads with RLock
	rs.mu.RLock()
	s, ok := rs.localMap[sessionID]
	rs.mu.RUnlock()
	if ok {
		return s, true
	}

	data, err := rs.client.Get(ctx, "vbgw:session:"+sessionID).Bytes()
	if err == redis.Nil || err != nil {
		return nil, false
	}

	var state SessionState
	if err := json.Unmarshal(data, &state); err != nil {
		return nil, false
	}

	state.aiPaused = state.AiPausedExport
	state.recordPath = state.RecordPathExport
	state.bridgedWith = state.BridgedWithExport

	return &state, true
}

func (rs *RedisStore) GetByFSUUID(ctx context.Context, fsUUID string) (*SessionState, bool) {
	// C-1 FIX: Protect local map reads with RLock
	rs.mu.RLock()
	sid, ok := rs.localByUUID[fsUUID]
	rs.mu.RUnlock()
	if ok {
		return rs.Get(ctx, sid)
	}

	sid, err := rs.client.Get(ctx, "vbgw:fsuuid:"+fsUUID).Result()
	if err == redis.Nil || err != nil {
		return nil, false
	}
	return rs.Get(ctx, sid)
}

func (rs *RedisStore) Release(ctx context.Context, sessionID string) {
	// C-1 FIX: Protect local map delete with full Lock
	rs.mu.Lock()
	s, loaded := rs.localMap[sessionID]
	if loaded {
		delete(rs.localMap, sessionID)
		if s.FSUUID != "" {
			delete(rs.localByUUID, s.FSUUID)
		}
	}
	rs.mu.Unlock()

	if loaded {
		if s.IvrEventCh != nil {
			close(s.IvrEventCh)
			s.IvrEventCh = nil
		}
		s.Cancel()

		pipe := rs.client.Pipeline()
		pipe.Del(ctx, "vbgw:session:"+sessionID)
		if s.FSUUID != "" {
			pipe.Del(ctx, "vbgw:fsuuid:"+s.FSUUID)
		}
		pipe.Decr(ctx, "vbgw:active_calls")
		if _, err := pipe.Exec(ctx); err != nil {
			slog.Error("Redis release pipeline failed", "session_id", sessionID, "err", err)
		}
	}
}

func (rs *RedisStore) Count(ctx context.Context) int64 {
	val, err := rs.client.Get(ctx, "vbgw:active_calls").Int64()
	if err != nil {
		return 0
	}
	return val
}

func (rs *RedisStore) WaitAllDrained(ctx context.Context, killFn func(fsUUID string)) {
	ticker := time.NewTicker(500 * time.Millisecond)
	defer ticker.Stop()

	for {
		// C-1 FIX: Protect len() read
		rs.mu.RLock()
		remaining := len(rs.localMap)
		rs.mu.RUnlock()

		if remaining == 0 {
			slog.Info("All local sessions drained")
			return
		}
		select {
		case <-ctx.Done():
			slog.Warn("Drain timeout", "remaining_local", remaining)
			// Collect sessions to kill (snapshot under lock)
			rs.mu.RLock()
			toKill := make([]*SessionState, 0, len(rs.localMap))
			for _, s := range rs.localMap {
				toKill = append(toKill, s)
			}
			rs.mu.RUnlock()

			for _, s := range toKill {
				if killFn != nil && s.FSUUID != "" {
					killFn(s.FSUUID)
				}
				rs.Release(context.Background(), s.SessionID)
			}
			return
		case <-ticker.C:
			slog.Info("Draining local sessions", "remaining", remaining)
		}
	}
}

func (rs *RedisStore) ForEachLocal(fn func(s *SessionState)) {
	// C-1 FIX: Snapshot under RLock, then iterate outside lock
	rs.mu.RLock()
	snapshot := make([]*SessionState, 0, len(rs.localMap))
	for _, s := range rs.localMap {
		snapshot = append(snapshot, s)
	}
	rs.mu.RUnlock()

	for _, s := range snapshot {
		fn(s)
	}
}

// SaveSession persists a session state update to Redis.
// Used by HandleLocalCommand after mutating session fields.
func (rs *RedisStore) SaveSession(ctx context.Context, s *SessionState) error {
	return rs.saveToRedis(ctx, s)
}
