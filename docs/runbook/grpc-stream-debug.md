# Runbook — VoicebotAiService gRPC 흐름 디버깅

> 배경: backend (`app/grpc_server/`) 가 `voicebot.ai.VoicebotAiService` 호스팅. vbgw_v2/bridge 가 client.
> 트래픽: bridge → backend gRPC :50051 (cluster-internal).

## 0. 확인 위치

| 무엇                  | 어디                                                                     |
|-----------------------|--------------------------------------------------------------------------|
| 서버 로그             | `kubectl -n agentoe logs -l app.kubernetes.io/name=agentoe-backend -f`   |
| gRPC 메트릭           | `agentoe_grpc_*` (Grafana — 별도 dashboard 권장. 현재는 Agentic 대시보드의 active_sessions) |
| SLO 백업 시리즈        | `agentoe_call_setup_total{result}`, `agentoe_call_terminations_total{reason}` |
| Helm health           | `kubectl -n agentoe rollout status deploy/agentoe-backend`              |
| Service grpc 포트     | `kubectl -n agentoe get svc agentoe-backend -o yaml | grep -A2 grpc`     |
| NetworkPolicy         | `kubectl -n agentoe describe networkpolicy agentoe-backend`              |

## 1. 빠른 sanity (Pod 안에서)

```bash
POD=$(kubectl -n agentoe get pod -l app.kubernetes.io/name=agentoe-backend -o name | head -1)

# grpc-health-check
kubectl -n agentoe exec -it $POD -- /bin/sh -c '
  python3 -c "
import grpc
from grpc_health.v1 import health_pb2, health_pb2_grpc
ch = grpc.insecure_channel(\"localhost:50051\")
st = health_pb2_grpc.HealthStub(ch)
resp = st.Check(health_pb2.HealthCheckRequest(service=\"voicebot.ai.VoicebotAiService\"), timeout=2)
print(\"status\", resp.status)
"'
# → status 1 (SERVING) 가 정상.
```

## 2. 외부에서 grpcurl 로 호출 (개발 환경)

```bash
kubectl -n agentoe port-forward svc/agentoe-backend 50051:50051 &

# Reflection 켜져 있으면 (settings.GRPC_REFLECTION_ENABLED=true)
grpcurl -plaintext localhost:50051 list
# voicebot.ai.VoicebotAiService
# grpc.health.v1.Health

grpcurl -plaintext localhost:50051 grpc.health.v1.Health/Check
# {"status":"SERVING"}
```

## 3. 흔한 문제 + 진단

### 3.1 `bridge` 가 connect 못 함 (`UNAVAILABLE: connection refused`)

```bash
# A) Service 가 grpc 포트 노출 중인지
kubectl -n agentoe get svc agentoe-backend -o jsonpath='{.spec.ports[*].name}{"\n"}'
# → http grpc  (둘 다 있어야 함)

# B) NetworkPolicy 가 차단 중인지
kubectl -n agentoe get networkpolicy agentoe-backend -o yaml | grep -A3 fromVbgwNamespace
# fromVbgwNamespace 가 빈 값이면 NetworkPolicy 가 grpc ingress 룰을 안 만듬 → 모두 차단.
# → values: networkPolicy.ingress.fromVbgwNamespace="vbgw-staging" 설정.

# C) Pod 자체가 grpc port LISTEN 중인지
kubectl -n agentoe exec -it $POD -- ss -tlnp | grep 50051
```

### 3.2 stream 즉시 끊어짐 (클라가 `RST_STREAM` 받음)

대개 server side 미들웨어 예외. backend 로그에서:

```bash
kubectl -n agentoe logs $POD --since=5m | jq -r 'select(.event=="StreamSession reader error" or .level=="ERROR")'
```

자주 보는 원인:
- `session_id` 비어 있음 → bridge 가 첫 청크에 SIP Call-ID 못 박음. bridge 측 PR.
- `restore_or_create_orchestrator` 실패 — Mongo connection 끊김. `agentic` 대시보드의 CB 패널 확인.
- ESO 시크릿 미동기화 — `kubectl -n agentoe get externalsecret`.

### 3.3 STT 성공인데 TTS 안 옴 (END_OF_TURN 만 옴)

```bash
# pipeline 로그 — degraded mode 진입했나?
kubectl -n agentoe logs $POD --since=5m \
  | jq -r 'select(.event | test("VENDOR_DEGRADED|TTS")) | .'
```
- `degraded_stage="tts"` 면 Google TTS Circuit Breaker OPEN. `agentic` 대시보드 → CB 패널 → `google-tts` 가 OPEN 인지.
- `degraded=true` 인데 stage 가 `stt` 면 STT 단계에서 fallback 메시지를 STT_RESULT 로 보냄, audio 없음 — 정상 (degraded path).

### 3.4 Barge-in 작동 안 함

bridge 가 새 발화 시 `is_speaking=true` AudioChunk 보내야 backend 가 `clear_buffer=true` 응답.

```bash
# AudioChunk 가 오는데 is_speaking transition 이 안 보이는지
kubectl -n agentoe logs $POD --since=2m \
  | jq -r 'select(.event=="audio_chunk_received") | .is_speaking'
```
모두 `false` 만 나오면 bridge 의 silero VAD 가 발화 감지 못 함 — vbgw_v2/bridge/internal/vad 점검.

### 3.5 stream 이 정상 종료 안 됨 (영원히 idle)

원인:
- bridge 가 client-side stream half-close 안 보냄. → bridge 코드 점검.
- backend SHUTDOWN_DRAIN_TIMEOUT 보다 stream 길어서 강제 끊김.

```bash
# 진행 중 stream 수
kubectl -n agentoe exec $POD -- curl -s localhost:8000/api/v1/metrics/prometheus \
  | grep agentoe_grpc_sessions_active
```

## 4. cutover 점검 — vbgw-ai 와 backend 양쪽 사용 시

**전환 직후 권장 점검 순서:**

```bash
# 1) backend 가 SLO 백업 시리즈 발화하는지
kubectl -n agentoe exec $POD -- curl -s localhost:8000/api/v1/metrics/prometheus \
  | grep -E 'agentoe_call_setup_total|agentoe_call_terminations_total|agentoe_call_duration_seconds'

# 2) vbgw 측 bridge 가 어느 endpoint 호출 중인지
kubectl -n vbgw-staging logs deploy/vbgw-bridge --since=5m | grep grpcAiAddr

# 3) bridge metrics — vbgw_grpc_calls_total{target} 가 있으면 분포 확인
```

## 5. 롤백 (긴급 시)

backend gRPC 자체를 끄고 vbgw-ai 로 회귀:

```bash
# A) backend 측 — 실시간 비활성
kubectl -n agentoe set env deploy/agentoe-backend GRPC_ENABLED=false
kubectl -n agentoe rollout status deploy/agentoe-backend
# → backend 는 gRPC port 안 LISTEN. bridge 가 자동으로 connection 실패 → vbgw-ai 폴백 (LB 정책에 따라).

# B) vbgw 측 — bridge 의 grpcAiAddr 를 vbgw-ai service 로 명시
helm -n vbgw-staging upgrade vbgw <chart> --set bridge.grpcAiAddr=ai-service:50051
```

## 6. 관련 문서

- 통합 가이드: `docs/guide/cross-project-integration.md`
- proto contract: `skeleton/contracts/proto/voicebot.proto`
- Servicer 코드: `skeleton/backend/app/grpc_server/voicebot_service.py`
- 메트릭 정의: `skeleton/backend/app/grpc_server/metrics.py`
- SLO 정의 (vbgw 시리즈): `docs/reference/slo.md` §2.5/2.6
