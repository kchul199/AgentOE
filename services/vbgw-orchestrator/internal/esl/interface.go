/**
 * @file interface.go
 * @description ESL Commander 인터페이스 — 테스트 mock 지원을 위한 추상화
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-09 | [Implementer] | 최초 생성 | api 패키지 테스트용 인터페이스 추출
 * v1.1.0 | 2026-04-09 | [Implementer] | FS-2,FS-3 | Eavesdrop, Conference, AttendedTransfer 추가
 * ─────────────────────────────────────────
 */

import (
	"context"
)

// Commander abstracts ESL commands for testability and tracing.
// *Client satisfies this interface.
type Commander interface {
	Originate(ctx context.Context, uuid, target, callerID string, useStandby bool) (string, error)
	SendDtmf(ctx context.Context, uuid, digits string) error
	Transfer(ctx context.Context, uuid, target string) error
	Bridge(ctx context.Context, uuidA, uuidB string) error
	Unbridge(ctx context.Context, uuid string) error
	RecordStart(ctx context.Context, uuid, path string) error
	RecordStop(ctx context.Context, uuid string) error
	Break(ctx context.Context, uuid string) error
	Kill(ctx context.Context, uuid string) error
	Dump(ctx context.Context, uuid string) (map[string]string, error)
	Pause(ctx context.Context) error
	Resume(ctx context.Context) error
	IsConnected() bool
	// FS-2: Supervisor monitoring
	Eavesdrop(ctx context.Context, supervisorUUID, targetUUID string) error
	ConferenceKick(ctx context.Context, confName, memberID string) error
	// FS-3: Attended (consultative) transfer
	AttendedTransfer(ctx context.Context, uuid, target string) error
}

// Verify *Client implements Commander at compile time.
var _ Commander = (*Client)(nil)
