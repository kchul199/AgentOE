# VoiceBot Gateway — Grafana PromQL 쿼리 모음

> **Version**: v1.0.0 | 2026-04-09 | DevOps

---

## 1. 동시 호 수 (실시간)
```promql
vbgw_active_calls
```

## 2. PDD (Post Dial Delay) P95
```promql
histogram_quantile(0.95, rate(vbgw_call_setup_duration_seconds_bucket[5m]))
```
알람: `> 3` (3초 초과 시 PBX 지연)

## 3. SIP 에러 코드 분포 (Top 5)
```promql
topk(5, sum by (sip_code, cause) (rate(vbgw_call_hangup_total[5m])))
```

## 4. SIP 503 비율
```promql
rate(vbgw_call_hangup_total{sip_code="503"}[5m])
```
알람: `> 0.05` (분당 3건 이상)

## 5. gRPC 스트림 상태
```promql
# 활성 세션
vbgw_grpc_active_sessions

# 에러율
rate(vbgw_grpc_stream_errors_total[5m])

# 프레임 드롭률
rate(vbgw_grpc_dropped_frames_total[5m])
```

## 6. ESL / Bridge 연결 상태
```promql
vbgw_esl_connected
vbgw_bridge_healthy
```
알람: `== 0 for 10s`

## 7. API 지연 시간 P95
```promql
histogram_quantile(0.95, sum by (le, path) (rate(vbgw_api_latency_seconds_bucket[5m])))
```

## 8. API Rate Limit 차단
```promql
rate(vbgw_admin_api_rate_limited_total[5m])
```

## 9. PBX 등록 상태
```promql
vbgw_sip_registered
vbgw_sip_registration_alarm
```
알람: `vbgw_sip_registration_alarm == 1`

## 10. VAD + Barge-in 이벤트
```promql
rate(vbgw_vad_speech_events_total[5m])
rate(vbgw_bargein_events_total[5m])
```

## 11. 통화 시간 분포
```promql
histogram_quantile(0.5, rate(vbgw_session_duration_seconds_bucket[5m]))
histogram_quantile(0.95, rate(vbgw_session_duration_seconds_bucket[5m]))
```

## 12. 녹음 정리 현황
```promql
rate(vbgw_recording_cleanup_files_total[1h])
rate(vbgw_recording_cleanup_bytes_total[1h])
```

---

## 알람 요약

| 메트릭 | 조건 | 심각도 | 대응 |
|--------|------|--------|------|
| `vbgw_esl_connected == 0` | 10초 이상 | CRITICAL | ESL 재연결 확인 |
| `vbgw_sip_registration_alarm == 1` | 즉시 | CRITICAL | PBX 연결 확인 |
| PDD P95 > 3s | 5분 지속 | HIGH | PBX/네트워크 지연 |
| `sip_code=503` rate > 0.05 | 5분 지속 | HIGH | PBX 과부하 |
| `active_calls >= MAX_SESSIONS` | 즉시 | WARN | 용량 확장 검토 |
| gRPC drop rate > 0.001/s | 5분 지속 | WARN | AI 서버 부하 |
