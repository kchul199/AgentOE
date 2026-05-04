# skeleton/vbgw/ — INTEGRATION TEST STUB ONLY

> ⚠️ **이 디렉토리는 실제 배포되지 않습니다.** 실 vbgw 는 별도 프로젝트.
> Real vbgw: `/Users/kchul199/vbgw_v2/`  (mounted at `mnt/vbgw_v2/` in this session)

## 무엇인가

`app/main.py` 는 backend 통합 테스트용 **mock gRPC + WebSocket 서버**.
- 4 개 포트 (gRPC 50051, WS 50052, health 8080, metrics 9100) 노출
- `VoicebotAiService.StreamSession` 의 placeholder 응답
- Prometheus metric 시리즈 (`agentoe_call_setup_total`, `agentoe_call_terminations_total`, `agentoe_call_duration_seconds`) 발화

## 왜 남겨두나

1. **Backend dev 환경**: 우리 backend 가 vbgw 없이도 통합 테스트 가능 (docker-compose 에서 띄움).
2. **Helm chart 검증**: `agentoe-vbgw` chart 가 실제 vbgw 와 contract drift 안 나는지 빌드 검증용.
3. **CI 안전망**: vbgw_v2 상태와 무관하게 우리 PR 이 머지 가능.

## 무엇이 아닌가

- 프로덕션 배포 대상 아님. ECR push 안 함.
- 실제 SIP/RTP/codec 처리 안 함.
- Bridge / FreeSwitch / Orchestrator 분리 구조 모방하지 않음.

## 실 구현 위치 (vbgw_v2)

```
vbgw_v2/
├── vbgw-ai/                — Go AI 엔진 (VoicebotAiService gRPC server :50051)
├── vbgw-freeswitch/
│   ├── orchestrator/       — Go ESL/Redis 기반 통화 라우팅 (REST :8080)
│   ├── bridge/             — Go WS↔gRPC bridge
│   └── (FreeSwitch 컨테이너)
└── charts/vbgw/            — 3-deployment Helm chart
```

## 향후 계획

AgentOE backend 가 `VoicebotAiService` 를 직접 구현하면 (현재 vbgw-ai 가 go-openai 직접 호출 → multi-tenant/agentic 없음), 이 placeholder 는 삭제.

## 관련 문서

- Cross-project integration: `skeleton/docs/guide/cross-project-integration.md`
- Canonical proto: `skeleton/contracts/proto/voicebot.proto`
- HANDOFF: `skeleton/docs/HANDOFF.md` §11 (cross-project)
