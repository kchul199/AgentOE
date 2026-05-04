/**
 * @file manager.go
 * @description 레거시 호환용 Manager 별칭 (실제 인터페이스는 repository.go의 Store)
 *
 * 변경 이력
 * ─────────────────────────────────────────
 * v1.0.0 | 2026-04-07 | 최초 생성 | NewManager + in-memory 세션
 * v1.2.0 | 2026-04-19 | 데드코드 정리 | Manager interface 제거, Store로 통합
 * ─────────────────────────────────────────
 */

package session

// Manager is a backward-compatible alias for Store.
// All new code should use Store directly.
type Manager = Store
