# VBGW Phase 2 Production SLA Baseline

이 문서는 VoiceBot Gateway (vbgw_v2)의 상용 운영을 위한 성능 기준(Service Level Agreement) 및 벤치마크 가이드라인을 정의합니다.

## 1. 지연 시간 (Latency)
모든 API 엔드포인트는 아래의 P95 지연 시간 기준을 준수해야 합니다.

| 엔드포인트 | 기준 (P95) | 비고 |
|------------|-----------|------|
| POST /api/v1/calls | < 150ms | PBX Originate 명령 발송 포함 |
| POST /api/v1/calls/{id}/dtmf | < 50ms | ESL API 전송 지연 포함 |
| POST /api/v1/calls/{id}/transfer | < 100ms | PBX 시그널링 지연 포함 |
| GET /health | < 20ms | 컴포넌트 상태 체크 지연 포함 |

## 2. 처리량 (Throughput)
| 지표 | 기준 | 비고 |
|------|------|------|
| 동시 통화수 (Sustained) | 1,000 CPS | 단일 노드 기준 (m5.xlarge급) |
| 통화 시도 (CAPS) | 50 CAPS | 초당 콜 생성 시도 횟수 |
| API TPS | 200 TPS | 관리 및 제어 API 총량 |

## 3. 안정성 및 성공률 (Reliability)
| 지표 | 기준 | 측정 도구 |
|------|------|-----------|
| API 성공률 (2xx/3xx) | > 99.9% | Prometheus |
| 콜 성공률 (ASR) | > 99% | CDR Webhook |
| VAD 처리 지연 (Hot Path) | < 20ms | Bridge Metrics |
| Barge-in 응답 지연 | < 100ms | E2E Measurement |

## 4. 검증 방법
K6 부하 테스트 스크립트(`test/benchmark/sla_verify.js`)를 사용하여 매 릴리즈마다 위 기준을 통과하는지 검증합니다.

```bash
k6 run test/benchmark/sla_verify.js
```
