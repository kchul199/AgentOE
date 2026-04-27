# Runbook — dev 환경 통합 테스트 (vbgw ↔ AgentOE backend)

> **언제 사용?** 운영 cutover (vbgw-ai-cutover.md) 실행 전 로컬에서 wire 검증.
> **얼마나 걸림?** 첫 실행 약 8~12분 (image build), 이후 1~2분.
> **무엇을 검증?** bridge 가 backend gRPC 로 실제로 도달 + StreamSession contract 정상 동작.

## 0. 전제

- **vbgw_v2 와 AgenticOE_v2 가 같은 컴퓨터의 형제 폴더** (`~/AgenticOE_v2`, `~/vbgw_v2`).
- Docker Desktop / Engine + Docker Compose v2.
- macOS / Linux. Apple Silicon 의 경우 `vbgw-ai` 가 linux/arm64 — `--platform` 자동 매칭.

## 1. 빠른 시작 (one-command)

```bash
cd ~/AgenticOE_v2/skeleton/scripts/integration
./dev-integration.sh up
```

이 명령이 다음을 순서대로 실행:

1. preflight (docker / compose / python / grpcio 점검)
2. 공유 docker network `agentoe-vbgw-bridge` 생성
3. AgentOE backend stack 기동 — mongo replica + redis + api + nginx
   - `docker-compose.integration.yml` override 가 `50051:50051` publish + 공유 network join
4. vbgw stack 기동 — freeswitch + bridge + orchestrator + redis (vbgw-ai 비활성)
   - `docker-compose.integration.yml` override 가 bridge env `AI_GRPC_ADDR=agentoe-api:50051` 로 redirect + 공유 network join
5. smoke gRPC client 실행 — 합성 통화 3건
6. bridge → backend wire 검증 (TCP 도달성)
7. 컨테이너 / network 상태 출력

**기대 결과** (마지막 부분):
```
[OK] call#1 session=smoke-... STT=... TTS=... EOT=1 ...
[OK] call#2 session=smoke-... STT=... TTS=... EOT=1 ...
[OK] call#3 session=smoke-... STT=... TTS=... EOT=1 ...
=== result: 3/3 OK ===
[OK] bridge → agentoe-api gRPC 도달 가능
[OK] 통합 smoke 모두 통과 — staging cutover 진행 가능
```

## 2. 단계별 수동 실행 (디버깅 시)

### 2.1 backend 만 띄우기 (vbgw 없이 wire 검증)
```bash
SKIP_VBGW=1 ./dev-integration.sh up
```
- backend stack 만 기동 + smoke client 가 backend gRPC 직접 호출
- bridge / FS 빌드 시간 (5-10분) 절약
- Phase Y 의 backend gRPC server 자체가 정상인지 빠르게 확인

### 2.2 컨테이너 상태 / 로그
```bash
./dev-integration.sh status                       # 한눈에 보기
./dev-integration.sh logs                          # api + bridge tail (Ctrl+C)
docker logs --tail 100 -f agentoe-api | jq        # backend 만
docker logs --tail 100 -f vbgw-bridge             # bridge 만
```

### 2.3 환경 정리
```bash
./dev-integration.sh down                          # 컨테이너 stop+rm, volume 보존
docker volume prune                                # mongo/redis 데이터까지 초기화 (주의)
docker network rm agentoe-vbgw-bridge              # 공유 network 도 제거
```

## 3. 무엇을 어떻게 검증하나

### 3.1 gRPC health (grpc_health_v1)
```bash
docker exec agentoe-api sh -c 'grpc_cli ls localhost:50051' 2>/dev/null \
  || python3 -c '
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
ch = grpc.insecure_channel("localhost:50051")
st = health_pb2_grpc.HealthStub(ch)
r = st.Check(health_pb2.HealthCheckRequest(service="voicebot.ai.VoicebotAiService"), timeout=2)
print("status:", health_pb2.HealthCheckResponse.ServingStatus.Name(r.status))
'
```
기대: `SERVING`.

### 3.2 reflection (dev only — `GRPC_REFLECTION_ENABLED=true` 필요)
```bash
grpcurl -plaintext localhost:50051 list
# voicebot.ai.VoicebotAiService
# grpc.health.v1.Health
# grpc.reflection.v1alpha.ServerReflection

grpcurl -plaintext localhost:50051 describe voicebot.ai.VoicebotAiService
```

### 3.3 StreamSession 합성 통화
- 우리 smoke client (`smoke_grpc_client.py`) 가 bridge 의 패턴을 모방
- 발화 chunk 10개 (200ms) → silence chunk 1개 → 응답 stream 수신
- 검증 항목:
  - `END_OF_TURN` 1개 받음 (필수)
  - `STT_RESULT` 또는 `TTS_AUDIO` 중 하나 이상 (필수)
  - elapsed_sec 합리적 (≤ 20s)

### 3.4 bridge → backend 연결성 (실 wire)
- bridge 컨테이너 안에서 `nc -z agentoe-api 50051` 가능해야 함
- 두 컨테이너가 같은 docker network 의 endpoint 가입 확인:
  ```bash
  docker network inspect agentoe-vbgw-bridge --format '{{range .Containers}}{{.Name}} {{.IPv4Address}}{{"\n"}}{{end}}'
  ```
  기대: `agentoe-api`, `vbgw-bridge` 둘 다 보임.

## 4. Compose 토폴로지

```text
┌──────── docker network: agentoe-net ────────┐
│  mongo-primary    mongo-secondary           │
│  redis            agentoe-api ◀──┐          │
│                                  │          │
└──────────────────────────────────┼──────────┘
                                   │ (공유 network 가입)
┌──── docker network: integration-bridge ────┐
│             agentoe-vbgw-bridge            │
│  agentoe-api  ◀──── vbgw-bridge ───────┐   │
└────────────────────────────────────────┼───┘
                                         │
┌──────── docker network: vbgw-net ──────┼──┐
│  freeswitch    redis (vbgw)            │  │
│  orchestrator  vbgw-bridge ◀───────────┘  │
│                (vbgw-ai 는 통합 시 비활성) │
└────────────────────────────────────────────┘

bridge env: AI_GRPC_ADDR=agentoe-api:50051   ← 공유 network 의 service alias
```

## 5. 흔한 실패 + 대응

| 증상                                                 | 원인 / 처치                                                            |
|------------------------------------------------------|-----------------------------------------------------------------------|
| smoke client `grpc UNAVAILABLE`                      | backend gRPC 가 안 떴거나 50051 publish 안 됨. `docker ps | grep agentoe-api` 의 PORTS 컬럼 확인 |
| smoke client timeout                                  | backend pipeline 이 hang. Mongo/Redis 미준비 가능. `agentoe-api` 로그 → `mongo-init` completed 인지 |
| bridge 가 `agentoe-api: name resolution failed`       | 공유 network 미가입. `docker inspect vbgw-bridge | jq '.[0].NetworkSettings.Networks'` |
| bridge 컨테이너가 vbgw-ai:8091 으로 향함 (override 무효) | compose 명령에 `-f docker-compose.integration.yml` 빠짐. 둘 다 명시 필수 |
| ESL_PASSWORD 누락으로 freeswitch 가 죽음               | `vbgw_v2/vbgw-freeswitch/.env` 작성 필요. `cp .env.example .env` 후 채움 |
| backend `pyopenssl/cryptography` 충돌                 | sandbox 환경 한정 — Docker 안에선 정상. local pip 환경 문제면 `pip install --upgrade pyopenssl` |
| smoke 가 STT/TTS 0 — "no STT_RESULT and no TTS_AUDIO" | dummy audio 라 STT 가 빈 응답. 그래도 backend 가 STT 호출하고 응답 받으면 통과해야 — `[ERROR]` 도 STT_RESULT 카운트됨. 0 이면 pipeline 실행 자체가 안 됨 (handle_audio buffer 미달 등) |

## 6. cutover 진행 게이트

이 통합 테스트가 **3 회 연속 모두 OK** 면 다음 진행:

1. AgenticOE_v2 측 PR 머지 (Phase Y/Z 변경)
2. vbgw_v2 측 PR 머지 (chart canary block + env key fix)
3. `docs/runbook/vbgw-ai-cutover.md` 의 Stage A (staging 100%) 부터 시작

## 7. 관련 문서

- `docs/runbook/vbgw-ai-cutover.md` — 운영 cutover 4 stage
- `docs/runbook/grpc-stream-debug.md` — staging/prod 의 gRPC 흐름 디버깅
- `docs/guide/cross-project-integration.md` — 두 프로젝트 책임 분담
- canonical proto: `skeleton/contracts/proto/voicebot.proto`
- backend Servicer: `skeleton/backend/app/grpc_server/voicebot_service.py`
- vbgw bridge config: `vbgw_v2/vbgw-freeswitch/bridge/internal/config/config.go`

## 8. 다음 개선 후보 (선택)

- 합성 STT 응답 — backend 가 mock STT 모드 (`STT_PROVIDER=mock`) 지원하면 dummy audio 로도 의미 있는 텍스트 응답
- 실제 wav 파일 입력 — `--audio-file /path/to/sample.wav` 옵션 (16kHz mono PCM 변환 후 chunk 분할)
- bridge 측 gRPC client 직접 사용 — `vbgw_v2/vbgw-freeswitch/bridge/internal/grpc/client.go` 단독 빌드해 smoke
- chaos — backend 재시작 / network 끊김 / SIGTERM 중간 동안 stream 동작 검증
