# Runbook: DLQ 처리 절차

| 항목 | 값 |
|---|---|
| 작성일 | 2026-02-05 |
| 최종 점검일 | 2026-04-18 |
| 대상 온콜 | platform-oncall, 장애 관련 팀 리드 |
| 관련 코드 | DLQ publisher 지점 (grep `dlq:`), `app/core/redis_client.py` |

## DLQ 가 무엇을 담는가

Dead Letter Queue 는 재시도 가능했지만 N 회(기본 3회) 실패한 비동기 작업을 격리 보관한다.

| DLQ 키 | 무엇을 담는가 | Publisher |
|---|---|---|
| `dlq:{tenant}:tool_invoke` | 외부 도구/커넥터 호출 실패 payload | `app/agentic/nodes/tool.py` |
| `dlq:{tenant}:transfer` | 상담원 전환(SIP REFER) 실패 요청 | `app/services/transfer_service.py` |
| `dlq:{tenant}:webhook_out` | 외부 webhook 송신 실패 | `app/services/webhook_service.py` |
| `dlq:{tenant}:scenario_publish` | 시나리오 publish 단계에서 실패한 후속 처리 | `app/repositories/scenario_repository.py` |

**형식**: `LPUSH` 로 JSON 문자열 추가. 각 항목은:
```json
{
  "ts": "2026-04-18T10:22:31Z",
  "attempt": 3,
  "tenant_id": "t_acme",
  "session_id": "s_...",
  "kind": "tool_invoke",
  "payload": { ... },
  "last_error": "timeout after 5s",
  "trace_id": "…"
}
```

## 언제 DLQ 를 본다

* 알림: `dlq_depth` Prometheus Recording Rule 이 테넌트별 임계치 초과 시
* 고객사 "특정 기능이 안 됩니다" 제보 시 — 해당 테넌트 DLQ 를 먼저 확인
* 주간 정기 점검: 월요일 오전 — 모든 DLQ depth 확인

## 조회

```bash
# 테넌트별 depth
redis-cli LLEN dlq:t_acme:tool_invoke

# 최신 항목 5 개 훑기
redis-cli LRANGE dlq:t_acme:tool_invoke 0 4 | jq .

# 전체 테넌트의 DLQ 목록
redis-cli --scan --pattern 'dlq:*:*'
```

## 카테고리별 대응

### 1) Tool invoke 실패

대부분 원인 = 외부 API 가용성 또는 자격 증명 문제.

1. `last_error` 필드 분포 확인:
   ```bash
   redis-cli LRANGE dlq:t_acme:tool_invoke 0 -1 \
     | jq -r '.last_error' | sort | uniq -c | sort -rn
   ```
2. 원인별:
   * `401/403` → 해당 도구의 자격 증명 회전. `connectors/*` 의 env 또는 Secret 갱신.
   * `timeout` → 대상 서비스 장애 가능. Circuit breaker 상태 (`agentoe_circuit_breaker_state{service="..."}`) 확인.
   * `404/schema mismatch` → 도구 계약이 깨짐. 코드 레벨 조치 필요 → 티켓 생성, 재처리 보류.
3. 재처리 가능하다면:
   ```bash
   python scripts/dlq_replay.py \
     --queue dlq:t_acme:tool_invoke \
     --limit 100 \
     --dry-run
   # dry-run 으로 결과 보고 이상 없으면 --commit 으로 재실행
   ```
4. 재처리 불가능 항목은 **삭제 전 S3 archive**:
   ```bash
   redis-cli LRANGE dlq:t_acme:tool_invoke 0 -1 \
     | aws s3 cp - s3://agentoe-dlq-archive/t_acme/tool_invoke/$(date -u +%Y%m%d).json
   redis-cli DEL dlq:t_acme:tool_invoke
   ```

### 2) Transfer 실패

1. VBGW (음성 게이트웨이) 로그와 cross-reference 하여 SIP 레벨 원인 확인.
2. "고객은 상담원 연결을 받지 못했다" 이므로 개별 case 확인 필요 — 자동 재시도 금지.
3. 원본 통화 session_id 로 오디오 녹음 조회, 보상(call back) 요청을 CS 팀으로 전달.

### 3) Webhook out 실패

1. 고객 webhook 엔드포인트 상태 확인 (HTTP 5xx/타임아웃).
2. 고객이 의도적으로 무시하는 이벤트라면 DLQ 를 **삭제해도 OK** (archive 후).
3. 중요한 이벤트 (결제 완료 등) 는 CS 경유 고객 재전송 조율.

### 4) Scenario publish 실패

코드 경로 버그 — 재처리보다 수정 후 재발행.
DLQ 는 포스트모템용 증거로 보존.

## 비상 정리

Redis 메모리 압박 시 — DLQ 전체를 S3 로 덤프 후 한 번에 비운다.
```bash
bash scripts/dlq_archive_all.sh
```
이후 반드시 블레임리스 리뷰: 왜 DLQ 가 이만큼 누적되었는가?

## 관련

* [Runbook: Redis 장애 대응](./redis-outage.md)
* `scripts/dlq_replay.py` — 재처리 도구
* Metric: `dlq_depth` recording rule in `prometheus/recording_rules.yml`
