# FreeSWITCH VoiceBot Gateway — Operations Runbook

> **Version**: v1.0.0 | 2026-04-07 | DevOps  
> **Parent**: `freeswitch_migration_v2.md`

---

## 1. Blue/Green 전환 절차

### 1.1 사전 검증

```bash
cd vbgw-freeswitch
./scripts/validate_prod.sh
```

모든 FAIL이 0건이어야 전환 가능합니다.

### 1.2 단계별 전환

| 단계 | 트래픽 | 모니터링 기간 | 명령 |
|------|--------|-------------|------|
| Stage 1 | 10% | 1시간 | `./scripts/cutover.sh 10` |
| Stage 2 | 25% | 1시간 | `./scripts/cutover.sh 25` |
| Stage 3 | 50% | 24시간 | `./scripts/cutover.sh 50` |
| Stage 4 | 100% | 7일 | `./scripts/cutover.sh 100` |

각 단계에서 SLO 위반 발생 시 자동으로 롤백 절차가 안내됩니다.

### 1.3 PBX 라우팅 변경

PBX/SBC 관리자가 수동으로 라우팅 비율을 변경합니다:

```
# 예시: Asterisk PBX dialplan
; Stage 1: 10% → FreeSWITCH
exten => _X.,1,GotoIf($[${RAND(1,10)} <= 1]?fs,1:cpp,1)
exten => _X.,n(fs),Dial(SIP/${EXTEN}@freeswitch-ip)
exten => _X.,n(cpp),Dial(SIP/${EXTEN}@cpp-vbgw-ip)
```

---

## 2. 일상 운영

### 2.1 서비스 시작/중지

```bash
# Development
docker compose up -d
docker compose down

# Production
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
```

### 2.2 Graceful Shutdown (콜 보존)

```bash
# 1. Orchestrator에 SIGINT 전송
docker compose exec orchestrator kill -INT 1

# 2. 로그에서 5단계 완료 확인
docker compose logs -f orchestrator | grep "Shutdown"
# [Shutdown 1/5] HTTP server: rejecting new requests
# [Shutdown 2/5] ESL: fsctl pause sent
# [Shutdown 3/5] Draining 3 sessions (timeout=30s)
# [Shutdown 4/5] Bridge gRPC streams closed
# [Shutdown 5/5] ESL connection closed

# 3. 전 서비스 중지
docker compose down
```

### 2.3 헬스체크

| 엔드포인트 | 용도 | 정상 |
|-----------|------|------|
| `GET /live` | 프로세스 생존 | 200 OK |
| `GET /ready` | ESL 연결 상태 | 200 OK |
| `GET /health` | 종합 (ESL + Bridge + 세션) | 200 JSON |
| `GET /metrics` | Prometheus 메트릭 | 200 text |

```bash
curl http://localhost:8080/live
curl http://localhost:8080/health | jq
curl http://localhost:8080/metrics | grep vbgw_active_calls
```

### 2.4 FreeSWITCH CLI 진단

```bash
docker compose exec freeswitch fs_cli -x "sofia status"
docker compose exec freeswitch fs_cli -x "show calls"
docker compose exec freeswitch fs_cli -x "status"
```

---

## 3. 모니터링 & 알람

### 3.1 핵심 메트릭 (Prometheus)

| 메트릭 | SLO | 알람 조건 |
|--------|-----|----------|
| `vbgw_active_calls` | - | > MAX_SESSIONS (100) |
| `vbgw_esl_connected` | - | = 0 for > 10s |
| `vbgw_grpc_dropped_frames_total` | ≤ 0.1% | rate > 0.001/s |
| `vbgw_grpc_stream_errors_total` | ≤ 0.1% | rate > 0.001/s |
| `vbgw_bargein_events_total` | - | 모니터링용 |
| `vbgw_admin_api_rate_limited_total` | - | rate > 1/s |

### 3.2 SLO 대시보드 (Grafana 쿼리)

```promql
# Health uptime (30일 롤링)
avg_over_time(up{job="orchestrator"}[30d]) * 100

# API P95 latency
histogram_quantile(0.95, rate(vbgw_api_latency_seconds_bucket[5m]))

# Audio drop rate
rate(vbgw_grpc_dropped_frames_total[5m])

# Active calls
vbgw_active_calls
```

### 3.3 실시간 모니터링

```bash
# SLO 모니터 (5초 간격 CSV)
./scripts/slo_monitor.sh 127.0.0.1 slo_live.csv

# 메트릭 워치
watch -n 5 'curl -s http://localhost:8080/metrics | grep -E "active_calls|dropped|errors|bargein"'
```

---

## 4. 롤백 절차 (RTO: 5분)

### 4.1 트리거 조건 (즉시 롤백)

- `/health` 가용률 < 99.9% (5분 윈도우)
- Audio queue drop rate > 1%
- gRPC stream failure rate > 0.5%
- P1 인시던트 (통화 품질 불만)

### 4.2 절차

```bash
# 자동 롤백 실행
./scripts/cutover.sh rollback

# 또는 수동 절차:
# 1. PBX 라우팅 즉시 C++ vbgw IP로 복구 (1-2분)
# 2. C++ 시스템은 이미 standby로 실행 중 — 즉시 트래픽 수신 가능
# 3. FreeSWITCH 스택 graceful stop
docker compose exec orchestrator kill -INT 1
# Shutdown 5/5 확인 후:
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
# 4. 로그/메트릭 수집 후 포스트모템
```

### 4.3 Critical Rule

> **기존 C++ 프로세스는 Phase 4 완료(100% 전환 후 7일) 시점까지 절대 종료하지 않습니다.**

---

## 5. 메모리 누수 감지

```bash
# 72시간 자동 수집 (4시간 간격)
./scripts/memory_monitor.sh 127.0.0.1 4 72

# 수동 비교
go tool pprof -diff_base results/memory_*/heap_0h.prof results/memory_*/heap_72h.prof

# goroutine leak 체크
go tool pprof results/memory_*/goroutine_72h.prof
```

**Pass 기준**: `inuse_space` 선형 증가 없음, goroutine 수 안정

---

## 6. 문제 해결

### 6.1 ESL 연결 실패

```
증상: /ready → 503, ESL disconnected
원인: FreeSWITCH 미기동 또는 ESL 포트(8021) 방화벽
조치:
  docker compose exec freeswitch fs_cli -x "status"
  docker compose logs freeswitch | tail -20
```

### 6.2 gRPC 연결 실패

```
증상: Bridge 로그에 "gRPC dial failed"
원인: AI Engine 미기동 또는 네트워크 문제
조치:
  curl -v telnet://AI_GRPC_ADDR  # 연결 확인
  docker compose logs bridge | grep "gRPC"
```

### 6.3 세션 용량 초과

```
증상: POST /api/v1/calls → 503 "capacity exceeded"
원인: MAX_SESSIONS(100) 도달
조치:
  curl http://localhost:8080/health | jq '.active_calls'
  # 세션 정리 대기 또는 MAX_SESSIONS 증가
```

### 6.4 Barge-in 지연

```
증상: TTS 중단까지 200ms 초과
원인: Bridge → Orchestrator HTTP 왕복 지연
조치:
  docker compose logs bridge | grep "Barge-in executed" | grep elapsed_ms
  # P95 > 100ms 시: loopback 배치 확인, 네트워크 지연 조사
```

---

## 7. PBX/SBC 연동 가이드 (Q-08, Q-10)

### 7.1 Answer Delay와 SBC 타이머 관계

```
ANSWER_DELAY_MS 설정 시 SBC Timer 확인 필수:
┌──────────────┬─────────────────────────────────────────┐
│ SBC 기종     │ 초기 응답 대기 타이머                      │
├──────────────┼─────────────────────────────────────────┤
│ Genesys SBC  │ 180 수신 후 별도 타이머 없음 (안전)       │
│ Oracle SBC   │ 180 수신 후 최대 180초 (안전)             │
│ Ribbon SBC   │ INVITE 후 4초 (180 없으면 CANCEL)        │
│ AudioCodes   │ 설정 가능 (기본 30초)                     │
│ 삼성 SCM     │ INVITE 후 10초 (180 없으면 CANCEL)       │
└──────────────┴─────────────────────────────────────────┘

현재 구현: ring_ready(180) 즉시 → sleep(200ms) → answer(200)
→ 200ms delay는 모든 SBC에서 안전합니다.
→ 500ms 이상으로 변경 시 Ribbon SBC/삼성 SCM 테스트 필수
```

### 7.2 게이트웨이 ping(OPTIONS)과 REGISTER 동시 사용 (Q-10)

```
게이트웨이 설정별 권장사항:
┌────────────────────────┬──────────────────────────┐
│ 설정 조합              │ 권장사항                   │
├────────────────────────┼──────────────────────────┤
│ register=true + ping   │ ping 간격 60초 이상 권장   │
│                        │ (일부 PBX 중복 등록 이슈)  │
│ register=false + ping  │ 안전 (ping만으로 헬스체크) │
│ register=true + no ping│ 안전 (REGISTER로 헬스체크) │
└────────────────────────┴──────────────────────────┘

ping-max=3: OPTIONS 3회 연속 실패 시 게이트웨이 DOWN 판정
ping-min=1: OPTIONS 1회 성공 시 게이트웨이 UP 복구
```

### 7.3 PBX 기종별 호환성 테스트 체크리스트

```
배포 전 반드시 검증:
□ SIP INVITE → 180 → 200 OK → RTP 양방향 확인
□ DTMF (RFC 2833): 1,2,3,*,#,0 전송 → IVR 반응 확인
□ 코덱 협상: G.711(PCMU) 우선 매칭 확인 (Wireshark SDP 캡처)
□ SRTP: optional 모드에서 키 교환 성공 확인
□ Session Timer: 30분 이상 통화 유지 테스트
□ Transfer (REFER): 상담원 전환 성공 확인
□ Hold/Resume: PBX에서 보류 → 음봇 AI 일시중단 → 보류 해제 → AI 재개
□ Graceful Shutdown: 통화 중 SIGINT → BYE 정상 전송 확인
□ 게이트웨이 failover: 주 PBX 다운 → 대기 PBX 자동 전환
□ 부하: 피크 시간 동시 콜 수 + 20% 마진으로 SIPp 테스트
```

### 7.4 방화벽/NAT TCP 세션 타이머 (S-04, S-05)

```
SIP TCP/TLS 연결은 idle 상태에서 방화벽에 의해 끊길 수 있습니다.
FreeSWITCH tcp-keepalive=60초가 설정되어 있어 대부분 안전합니다.

방화벽별 TCP 세션 타이머:
┌──────────────────┬─────────────────────┬─────────────────┐
│ 환경             │ 기본 TCP 타이머     │ keepalive=60 OK?│
├──────────────────┼─────────────────────┼─────────────────┤
│ AWS Security Group│ 350초 (약 6분)     │ ✓ (충분)        │
│ Azure NSG        │ 4분                 │ ✓               │
│ GCP Firewall     │ 10분                │ ✓               │
│ 일반 방화벽      │ 30분~1시간          │ ✓               │
│ NAT 라우터       │ 5분~30분            │ ✓               │
│ 엄격한 방화벽    │ 2분                 │ ✓ (60초 < 2분)  │
└──────────────────┴─────────────────────┴─────────────────┘

NAT 환경 (S-04):
  EXTERNAL_RTP_IP와 EXTERNAL_SIP_IP를 반드시 공인 IP로 설정하세요.
  auto-nat 사용 시 Docker host 내부 IP가 SDP에 포함되어
  SBC가 RTP를 보낼 주소를 잘못 인식 → one-way audio 발생.

  확인: curl ifconfig.me
  설정: .env에 EXTERNAL_RTP_IP=<공인IP>, EXTERNAL_SIP_IP=<공인IP>
```

### 7.5 SIP 실패 코드 대응 가이드 (S-01)

```
Grafana에서 vbgw_call_hangup_total 메트릭으로 모니터링:

┌────────────┬──────────────────────┬────────────────────────┐
│ SIP Code   │ Hangup Cause         │ 대응                    │
├────────────┼──────────────────────┼────────────────────────┤
│ 200        │ NORMAL_CLEARING      │ 정상 종료              │
│ 486        │ USER_BUSY            │ PBX 회선 포화 확인     │
│ 408        │ NO_ANSWER            │ PBX 응답 불능 확인     │
│ 480        │ NO_USER_RESPONSE     │ 상담원 미응답 (전환 실패)│
│ 503        │ NORMAL_TEMP_FAILURE  │ PBX 과부하 → failover  │
│ 603        │ CALL_REJECTED        │ PBX 수신 거부 정책     │
│ unknown    │ ORIGINATOR_CANCEL    │ 고객이 먼저 끊음       │
└────────────┴──────────────────────┴────────────────────────┘

알람 조건:
  rate(vbgw_call_hangup_total{sip_code="503"}[5m]) > 0.05  → PBX 과부하
  rate(vbgw_call_hangup_total{sip_code="486"}[5m]) > 0.1   → 회선 포화
  vbgw_sip_registration_alarm == 1                          → 게이트웨이 다운

PDD (콜 셋업 시간) 알람:
  histogram_quantile(0.95, rate(vbgw_call_setup_duration_seconds_bucket[5m])) > 3
  → PDD P95가 3초 초과 시 PBX/네트워크 이상
```
