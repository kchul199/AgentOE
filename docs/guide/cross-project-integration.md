> ⚠️ **이 문서는 monorepo 통합 (Phase M, 2026-04-28) 후 history 보존용으로만 남겨두었습니다.**
> 옛 AgenticOE_v2 ↔ vbgw_v2 cross-project 통합 가이드. **현재는 단일 repo `agentoe`**.
> 새 작업은 `docs/HANDOFF.md` 와 `docs/guide/monorepo-migration-plan.md` 참고.

---


# Guide — AgenticOE_v2 ↔ vbgw_v2 통합

> 두 프로젝트는 **같은 사람** (charls) 이 owner 이지만 **별도 git 저장소**.
> Contract owner = AgenticOE_v2. vbgw_v2 는 consumer.

## 1. 두 프로젝트 책임 분담

| 프로젝트         | 언어            | 책임                                                                                  |
|------------------|-----------------|---------------------------------------------------------------------------------------|
| **AgenticOE_v2** | Python (FastAPI) + React + Helm/Terraform | Agentic 오케스트레이션, multi-tenant, JWKS, scenarios (Mongo), idempotency, SLO/observability, **proto contract owner**, 인프라 |
| **vbgw_v2**      | Go + FreeSwitch | SIP/RTP signaling, codec 변환, ESL orchestrator, WS↔gRPC bridge, AI 엔진 (현재는 go-openai 직접) |

## 2. 런타임 데이터 흐름

```text
PBX/SBC ──SIP/RTP──▶ FreeSwitch (vbgw_v2/vbgw-freeswitch)
                       │
                       ├─ ESL ──▶ Orchestrator (Go, REST :8080) ──▶ Redis (state)
                       │                              │
                       │                              ▼
                       │                       Admin Dashboard (REST API + JWT)
                       │
                       └─ WebSocket ──▶ Bridge (Go)
                                          │
                                          │ gRPC StreamSession (VoicebotAiService)
                                          │
                                          ▼
                              ┌─────────────────────────────────┐
                              │ 현재 (Phase 3 시점)             │
                              │   AI Engine = vbgw-ai (Go)      │
                              │   → go-openai 직접 호출         │
                              │   → 멀티테넌트/시나리오 없음     │
                              └─────────────────────────────────┘
                                            │
                                            ▼
                              ┌─────────────────────────────────┐
                              │ 향후 목표                        │
                              │   AgentOE backend 가             │
                              │   VoicebotAiService 직접 구현    │
                              │   → 시나리오/quota/JWKS 모두 적용 │
                              └─────────────────────────────────┘
```

## 3. Proto Contract — 단일 진실 소스

**Owner: `AgenticOE_v2/skeleton/contracts/proto/voicebot.proto`**

이 파일이 canonical. vbgw_v2 의 다음 3 곳은 sync 대상:

| 위치                                                  | 용도                       | go_package                  |
|-------------------------------------------------------|----------------------------|-----------------------------|
| `vbgw-ai/proto/voicebot.proto`                        | AI 엔진 (gRPC server)      | `vbgw-ai/proto/voicebot`    |
| `vbgw-freeswitch/protos/voicebot.proto`               | 참조용 (FS 빌드 컨텍스트)  | `vbgw-bridge/proto/voicebot` |
| `vbgw-freeswitch/bridge/proto/voicebot/voicebot.proto`| Bridge (gRPC client)       | `vbgw-bridge/proto/voicebot` |

go_package 만 의도된 차이. 그 외 message/service/field 는 **반드시 동일**.

### 3.1 변경 절차

```bash
# AgenticOE_v2 측에서 proto 수정
$EDITOR skeleton/contracts/proto/voicebot.proto

# stub 재생성 (backend 가 import)
cd skeleton/contracts && make gen-python && make gen-go

# vbgw_v2 의 3 곳에 동기화 — go_package 는 자동 치환
make sync-vbgw VBGW=/Users/kchul199/vbgw_v2

# 차이 검증 (CI 에서도 동일)
make verify-vbgw VBGW=/Users/kchul199/vbgw_v2

# 양쪽 PR
#   1) AgenticOE_v2 PR: proto 변경 + gen/* 갱신
#   2) vbgw_v2 PR: 동기화된 proto + 재생성된 *.pb.go
```

### 3.2 호환성 규칙

- **field 추가는 항상 OK** (proto3 default 보장).
- **field 삭제 / 타입 변경 / 번호 재사용 금지**.
- 새 RPC 는 새 service 또는 새 method (기존 method signature 변경 금지).
- breaking change 가 불가피하면: v2 namespace (`voicebot.ai.v2`) 신설 + 양쪽 1 release 동시 지원 후 v1 deprecate.

## 4. 이미지 / 배포 책임 분담

| 항목                   | AgenticOE_v2                   | vbgw_v2                                  |
|------------------------|---------------------------------|------------------------------------------|
| 이미지 빌드            | backend, frontend (and test stub vbgw) | vbgw-ai, vbgw-orchestrator, vbgw-bridge, freeswitch |
| ECR push               | `AgenticOE_v2` GHA (`build-images.yml`) | `vbgw_v2` 자체 CI                        |
| 이미지 tagging         | git short sha (main) / vX.Y.Z (tag) | (vbgw_v2 정책 — 현재는 `latest` 위주)    |
| Helm chart             | `agentoe-{backend,frontend}` (vbgw 는 deprecated) | `vbgw_v2/charts/vbgw/` (3 deployment)   |
| Helm 배포              | `AgenticOE_v2` GHA              | (vbgw_v2 측 배포 워크플로 필요)          |
| 클러스터 / namespace   | `agentoe-staging` / `agentoe`   | (별도 namespace 권장: `vbgw-staging` / `vbgw`)  |

> 둘 다 같은 EKS 클러스터에 배포. 다른 namespace 로 분리해 RBAC / NetworkPolicy 깔끔.

## 5. Service discovery (런타임 endpoint)

vbgw_v2 의 bridge 가 AI 엔진을 호출할 때 사용하는 주소:
```yaml
# vbgw_v2/charts/vbgw/values.yaml
bridge:
  grpcAiAddr: "ai-service:50051"   # 기본값. 클러스터 내부 DNS.
```

**Phase Y 후 (현재):**
- `agentoe-backend` 가 `VoicebotAiService` 구현 완료 (`backend/app/grpc_server/`). gRPC port 50051 노출 (`Service.ports[grpc]`).
- 전환 시: `bridge.grpcAiAddr` 를 `agentoe-backend.agentoe-staging.svc.cluster.local:50051` (staging) 또는 `agentoe-backend.agentoe.svc.cluster.local:50051` (prod) 로 변경.
- vbgw-ai deployment 는 cutover 후 deprecate. canary: 두 endpoint 사이 weighted gRPC LB (Linkerd / istio 또는 envoy) 권장.

**legacy (참고만):**
- `app/core/config.py` 의 `VBGW_GRPC_ENDPOINT` 는 backward-compat placeholder — backend → vbgw outbound 호출용 자리지만 현재 미사용.

## 6. 릴리즈 coordination

### 6.1 일반적 (proto 변경 없음)
- 양쪽 독립 배포 OK. AgentOE 가 먼저 배포되어도 vbgw 동작에 영향 없음 (현재는).

### 6.2 proto 변경 동반
- **반드시 호환 변경 (field 추가만).** 호환되면 순서 무관.
- breaking change 면 ordered rollout:
  1. proto v2 추가 (양쪽 v1+v2 동시 지원)
  2. vbgw 측 v2 마이그레이션 + 배포
  3. AgentOE 측 v1 deprecate + 배포
  4. vbgw 측 v1 코드 제거

### 6.3 인프라 변경
- AgentOE 가 EKS 클러스터 / VPC / IAM owner. vbgw 가 namespace 만 받아 배포.
- 새 IRSA / 시크릿 자리 필요 시: AgentOE 측 Terraform PR → output 공유 → vbgw 측 chart 갱신.

## 7. 운영 — 인시던트 대응

- **Slack `#ops-incident`** 는 양쪽 공유. SIP/RTP 문제는 vbgw 책임, agentic 응답 품질은 AgentOE 책임.
- 통화 1건의 trace 흐름:
  1. SIP Call-ID = vbgw orchestrator 의 session_id
  2. 같은 ID 가 gRPC `AudioChunk.session_id` 로 전달
  3. AgentOE 가 (장차) 이 ID 를 trace_id 로 사용 — 로그 grep 으로 cross-project 추적
- vbgw SLO (call setup ratio, mid-call drop) 는 AgenticOE_v2/`docs/reference/slo.md` §2.5/§2.6 정의.
  실제 메트릭은 vbgw_v2 가 노출해야 함 — `agentoe_call_setup_total{result}`, `agentoe_call_terminations_total{reason}` 시리즈.
  현재 vbgw_v2 가 이 시리즈를 노출하는지 확인 필요 (TODO: vbgw 측 metrics audit).

## 8. 알려진 정합성 이슈 (현재 상태)

| 이슈                                                                            | 영향                          | 조치 후보                              |
|---------------------------------------------------------------------------------|-------------------------------|----------------------------------------|
| ✅ ~~vbgw_v2/CLAUDE.md 가 stale~~                                               | 해결                          | Phase Z-D 에서 Go+FreeSwitch 실 구조로 갱신 |
| vbgw_v2 의 metric 시리즈가 우리 SLO doc 의 시그니처와 일치하는지 검증 안 됨      | SLO recording rule 분모 0 (완화: backend 가 백업 시리즈 발화) | vbgw 측 prometheus exporter audit |
| `agentoe-vbgw` Helm chart 가 vbgw_v2/charts 와 분리됨 (drift 위험)              | reference 만 — 배포 안 함     | 6개월 후 placeholder 완전 삭제          |
| ECR namespace 가 분리됨 (`agentoe-staging/vbgw` vs vbgw_v2 own ECR)             | 이미지 위치 혼란              | vbgw_v2 도 같은 ECR 사용하도록 통일     |
| ✅ ~~AgentOE backend 가 VoicebotAiService 미구현~~                              | 해결 (Phase Y) — backend `:50051` 노출 | `runbook/vbgw-ai-cutover.md` 따라 운영 |
| ✅ ~~vbgw chart `GRPC_AI_ADDR` env 버그~~                                        | 해결 (Phase Z-B) — `AI_GRPC_ADDR` 로 수정 | — |
| vbgw chart 에 canary 블록 추가됨 (Phase Z-B)                                    | cutover 인프라 준비 완료       | 실제 cutover 실행은 운영팀 결정 (4단계 stage) |

## 9. 자주 쓰는 명령

```bash
# proto sync
cd AgenticOE_v2/skeleton/contracts
make sync-vbgw VBGW=/Users/kchul199/vbgw_v2
make verify-vbgw VBGW=/Users/kchul199/vbgw_v2

# vbgw_v2 빠른 빌드 (Go 측)
cd vbgw_v2/vbgw-ai && go build -o ./ai_engine ./cmd
cd vbgw_v2/vbgw-freeswitch/bridge && go build -o ./bridge ./cmd
cd vbgw_v2/vbgw-freeswitch/orchestrator && go build -o ./orch_app ./cmd

# 양쪽 함께 docker-compose 로 띄우기 (vbgw_v2 측)
cd vbgw_v2/vbgw-freeswitch && docker-compose up -d
```

## 10. 참고 — 디렉토리 매핑

세션 환경에서:

| Host                        | VM (이 세션)                                        |
|-----------------------------|-----------------------------------------------------|
| `~/AgenticOE_v2`            | `/sessions/eloquent-beautiful-hopper/mnt/AgenticOE_v2` |
| `~/vbgw_v2`                 | `/sessions/eloquent-beautiful-hopper/mnt/vbgw_v2`     |

새 세션에서 `vbgw_v2` 폴더가 mount 안 되어 있으면 `request_cowork_directory` 도구로 추가 mount.
