# VoiceBot Gateway — 트러블슈팅 가이드

> **Version**: v1.0.0 | 2026-04-09 | DevOps

---

## 증상별 진단 매트릭스

### 1. One-Way Audio (한쪽만 들림)

| 확인 항목 | 명령 | 정상 | 비정상 시 조치 |
|-----------|------|------|---------------|
| NAT IP 설정 | `grep EXTERNAL_RTP_IP .env` | 공인 IP 명시 | `auto-nat` → 공인 IP로 변경 |
| RTP 포트 방화벽 | `nc -zu <IP> 16384-16584` | 열려있음 | 방화벽에 UDP 포트 범위 오픈 |
| SDP 확인 | `fs_cli -x "sofia global siptrace on"` | 공인 IP 포함 | EXTERNAL_RTP_IP 설정 확인 |
| SRTP 불일치 | Wireshark SIP INVITE 캡처 | SRTP suites 매칭 | `SRTP_MODE=optional`로 변경 |

### 2. 통화 끊김 (비정상 종료)

| 확인 항목 | 명령 | 정상 | 비정상 시 조치 |
|-----------|------|------|---------------|
| Session Timer | `grep SESSION_TIMEOUT .env` | 1800 또는 0 | 0으로 설정 (비활성화) |
| RTP Timeout | `fs_cli -x "sofia status profile internal"` | rtp-timeout=30 | ghost call 정리 정상 동작 |
| ESL 연결 | `curl localhost:8080/ready` | 200 | ESL 재연결 확인 |
| gRPC 스트림 | Bridge 로그 grep "gRPC recv error" | 없음 | AI 엔진 상태 확인 |
| PBX CANCEL | FS 로그 grep "CANCEL" | 정상 | answer_delay 200ms 이하 유지 |

### 3. DTMF 미작동

| 확인 항목 | 명령 | 정상 | 비정상 시 조치 |
|-----------|------|------|---------------|
| RFC 2833 | Wireshark RTP payload type 101 | PT=101 패킷 존재 | `dtmf-type=rfc2833` 확인 |
| In-band fallback | `grep liberal-dtmf internal.xml` | true | liberal-dtmf=true 설정 |
| IVR 상태 | Orchestrator 로그 "IVR DTMF" | digit 수신 로그 | start_dtmf가 audio_fork 앞인지 확인 |
| Bridge 전달 | Bridge 로그 "DTMF forwarded" | digit 전달 로그 | gRPC 스트림 상태 확인 |

### 4. AI 응답 지연

| 확인 항목 | 명령 | 정상 | 비정상 시 조치 |
|-----------|------|------|---------------|
| VAD | Bridge 로그 "VAD" | speech 감지 정상 | VAD threshold 조정 |
| gRPC 지연 | Bridge 로그 "gRPC send" 타임스탬프 | < 50ms | AI 서버 부하 확인 |
| TTS 버퍼 | Bridge 로그 "TTS channel full" | 없음 | AI TTS 응답 속도 확인 |
| Barge-in | `curl metrics \| grep bargein` | 이벤트 수 정상 | clear_buffer 동작 확인 |

### 5. 503 Service Unavailable

| 확인 항목 | 명령 | 정상 | 비정상 시 조치 |
|-----------|------|------|---------------|
| 세션 용량 | `curl health \| jq .active_calls` | < MAX_SESSIONS | MAX_SESSIONS 증가 또는 세션 정리 대기 |
| ESL 연결 | `curl ready` | 200 | FS 상태 확인 |
| Sofia 상태 | `fs_cli -x "sofia status"` | RUNNING | Sofia 프로파일 재로드 |
| Rate Limit | `curl metrics \| grep rate_limited` | 0 | RATE_LIMIT_RPS 증가 |

---

## 로그 위치

| 서비스 | 로그 경로 | Docker 명령 |
|--------|----------|-------------|
| FreeSWITCH | `./logs/freeswitch/` | `docker compose logs freeswitch` |
| Orchestrator | `./logs/orchestrator/` | `docker compose logs orchestrator` |
| Bridge | `./logs/bridge/` | `docker compose logs bridge` |

---

## SIP 디버깅

```bash
# SIP trace 활성화 (FreeSWITCH CLI)
docker compose exec freeswitch fs_cli -x "sofia global siptrace on"

# 특정 콜 덤프
docker compose exec freeswitch fs_cli -x "uuid_dump <FS-UUID>"

# 활성 채널 목록
docker compose exec freeswitch fs_cli -x "show channels"
```

---

## TLS 인증서 갱신

```bash
# 인증서 만료일 확인
openssl x509 -enddate -noout -in config/freeswitch/tls/agent.pem

# 인증서 갱신 후 FreeSWITCH 재로드
docker compose exec freeswitch fs_cli -x "sofia profile internal restart"

# 또는 전체 재시작 (graceful)
docker compose exec orchestrator kill -INT 1
# Shutdown 5/5 확인 후:
docker compose restart freeswitch
docker compose restart orchestrator bridge
```

---

## 성능 튜닝 가이드

### Jitter Buffer
| 환경 | JB_INIT | JB_MIN | JB_MAX |
|------|---------|--------|--------|
| 전용선 (< 5ms jitter) | 40 | 20 | 100 |
| 기본 (< 20ms jitter) | 60 | 20 | 200 |
| 인터넷 (> 30ms jitter) | 100 | 60 | 500 |

### 동시 호 수
- `MAX_SESSIONS`: Orchestrator 세션 제한 (기본 100)
- `max-sessions` in internal.xml: Sofia SIP 제한 (기본 110, MAX_SESSIONS + 10% 마진)
- RTP 포트: `(RTP_PORT_MAX - RTP_PORT_MIN) / 2 = 최대 동시 호`

### gRPC 스트림
- `GRPC_STREAM_DEADLINE_SECS`: 최대 통화 시간 * 1.5 (기본 7200 = 2시간)
- `GRPC_MAX_RETRIES`: 재연결 시도 횟수 (기본 5)
- `GRPC_MAX_BACKOFF_MS`: 최대 재연결 간격 (기본 4000ms)
