# Agentic Callbot Project Rules — agentoe monorepo

## 절대 규칙 (위반 시 머지 금지)
- **Performance First** — 모든 I/O (STT, LLM, DB, 외부 API) 는 비동기(async/await) 기반 non-blocking.
- **Latency is King** — 실시간 콜봇이므로 불필요한 루프나 지연을 유발하는 코드는 절대 금지.
- **Error Handling** — 에이전트 도구 호출(Tool Calling) 실패 시, 고객의 통화가 끊기지 않도록 우아한 Fallback 시나리오 필수.

## 새 세션 진입 시 — 이 두 파일을 먼저 읽어라
1. **`docs/HANDOFF.md`** — 현재 phase 진행 상황, 디렉토리 지도, 함정/결정사항, 다음 단계 후보, 시작 checklist. 캐노니컬.
2. **`docs/reference/slo.md`** — SLO 임계값 (모든 알람/canary 게이트의 단일 진실 소스).

`HANDOFF.md` 가 가장 최신 상태를 항상 반영한다. 작업 진행 시 phase 끝마다 §3 / §6 / §9 갱신.

## 코드 위치 (monorepo)

```
services/{backend,frontend,vbgw-ai,vbgw-bridge,vbgw-orchestrator,freeswitch,_test-stub}
contracts/proto/voicebot.proto       # canonical gRPC 계약
deploy/{terraform,k8s-bootstrap,helm,observability}
docker/compose.{backend,vbgw,integration}*.yml
docs/{business,adr,guide,reference,runbook,performance,reports}
.github/{workflows,actions}
legacy/                              # 옛 vbgw C++ PJSIP — 빌드 안 함
```

자세한 트리는 `docs/HANDOFF.md` §2 참고.

## 책임 분담 (한 줄)

- **services/backend** (Python FastAPI) — multi-tenant, agentic 시나리오, JWKS auth, quota, gRPC server (VoicebotAiService).
- **services/vbgw-bridge** (Go) — FreeSwitch audio_fork ↔ backend gRPC. VAD (silero) + barge-in.
- **services/vbgw-orchestrator** (Go) — ESL + Redis (Lua atomic) 통화 라우팅 + admin REST.
- **services/vbgw-ai** (Go) — legacy AI engine (go-openai 직접). cutover 후 deprecate.
- **services/freeswitch** — SIP/RTP signaling + media. 통신사/SBC 와 직접 연동.
- **services/frontend** (React SPA) — 시나리오 빌더 UI.

## Cross-cutting 규칙

- **Proto 변경**: `contracts/proto/voicebot.proto` 수정 → `cd contracts && make gen` → 같은 PR 에 stub 갱신 commit. CI 의 `contracts-gen` job 이 drift 검증.
- **Helm chart 변경**: `deploy/helm/` 의 chart + `values/{staging,prod}/*.values.yaml` 같이 갱신. CI 의 helm-lint matrix 가 모든 chart × env 검증.
- **Go module path**: `github.com/kchul199/agentoe/services/<svc>`. 새 service 추가 시 동일 패턴.
- **Python import path**: `app.<module>` (services/backend 안에서 PYTHONPATH 자동).

## 절대 안 하는 것 (장기 누적)

- 하드코딩 IP/포트/secret — env 또는 config
- `panic()` / blocking I/O — 위 절대 규칙 위반
- `legacy/` 안 파일 수정 — 참조 only
- `silero_vad.onnx` 수정 — binary
- proto 변경 시 stub 만 수정 (proto 도 같이) — drift
- staging 검증 없이 prod 배포 — runbook 없으면 X
