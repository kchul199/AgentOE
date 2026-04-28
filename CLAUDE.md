# Agentic Callbot Project Rules

## 절대 규칙 (위반 시 머지 금지)
- Performance First: 모든 I/O 작업(STT, LLM 호출, DB 등)은 철저하게 비동기(Async/Await) 기반의 non-blocking으로 작성할 것.
- Latency is King: 실시간 콜봇이므로 불필요한 루프나 지연을 유발하는 코드는 절대 금지.
- Error Handling: 에이전트 도구 호출(Tool Calling) 실패 시, 고객의 통화가 끊기지 않도록 우아한 Fallback 시나리오를 반드시 구현할 것.

## 새 세션 진입 시 — 이 두 파일을 먼저 읽어라
1. **`docs/HANDOFF.md`** — 현재 phase 진행 상황, 디렉토리 지도, 함정/결정사항, 다음 단계 후보, 시작 checklist. 캐노니컬.
2. **`docs/reference/slo.md`** — SLO 임계값 (모든 알람/canary 게이트의 단일 진실 소스).

`HANDOFF.md` 가 가장 최신 상태를 항상 반영한다. 작업 진행 시 phase 끝마다 §3 / §6 / §9 갱신.

## 코드 위치
- 모든 코드/배포 자산은 `` 안 (history 적인 이름).
- 비즈니스 docx/xlsx 는 `docs/` 안.
- 자세한 트리는 HANDOFF.md §2 참고.
