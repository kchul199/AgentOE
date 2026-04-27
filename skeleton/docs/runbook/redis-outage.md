# Runbook: Redis 장애 대응

| 항목 | 값 |
|---|---|
| 작성일 | 2026-02-02 |
| 최종 점검일 | 2026-04-18 |
| 대상 온콜 | platform-oncall |
| 관련 코드 | `app/core/redis_client.py`, `app/core/quota.py`, `app/domain/circuit_breaker.py` |

## 무슨 일이 벌어지는가

Redis 는 AgentOE 에서 다음을 담당한다:

1. Quota 카운터 (`quota:{tenant}:{scope}:{day}`)
2. Session KV (통화 중 상태)
3. Circuit breaker state
4. Kill-switch 상태 (`killswitch:global`, `killswitch:tenant:*`)
5. DLQ 리스트

**완전 장애 시 통화 자체는 살아있어야 한다.** 디자인 원칙:

* Quota check 실패 → **허용 후 경고 로그**. 서비스 가용성이 최우선 (과금은 나중에 reconcile).
* Session KV 실패 → in-memory fallback 으로 전환, 통화 내 일관성만 보장.
* CB state 실패 → conservative 값으로 open — 즉, 외부 호출 차단이 아니라 fail-open 으로 현재 통화는 계속.

## 탐지

다음 경보 중 하나 이상이 울리면 이 런북 적용:

* Prometheus — `up{job="redis"} == 0` 이 2분 이상
* `agentoe_pipeline_calls_total{status="degraded"}` 의 rate 가 baseline 대비 3× 이상
* Application log — `redis connection error` 로그 연속 ≥ 10 회 / 1분
* Grafana 보드: "Redis Saturation" (TODO: link)

## 즉시 대응 (0~5분)

1. **통화 볼륨 확인**
   ```bash
   kubectl -n agentoe exec deploy/backend-api -- \
     curl -s http://localhost:8000/api/v1/health | jq .
   ```
   `status: degraded` 이면 애플리케이션은 이미 자동 전환 중.
2. **Sentinel / Managed Redis 상태 체크**
   ```bash
   # Managed (예: AWS ElastiCache) 인 경우
   aws elasticache describe-replication-groups --replication-group-id agentoe-prod
   # self-hosted 인 경우
   kubectl -n data get po -l app=redis -o wide
   ```
3. **통화 손실이 발생하고 있는가?** — `agentoe_active_sessions` 가 급감하는지 확인. 급감 시 전체 P1 인시던트 선언.

## 격리 (5~15분)

### A. Redis 가 부분 장애 (reads OK, writes fail)

1. `redis-cli INFO replication` 으로 역할 확인.
2. Quota writes 는 자동으로 skip 된다 (`quota.py` 의 try/except 블록).
   → 그대로 두어도 통화에 영향 없음.
3. DLQ 쓰기가 실패하면 메시지가 메모리에 누적될 수 있음.
   `docker logs backend-api | grep "dlq enqueue failed"` 카운트를 체크.

### B. Redis 완전 장애

1. 프런트도어 kill-switch 활성화하여 신규 통화 제한:
   ```bash
   # NOTE: killswitch 자체도 Redis 에 있음. Redis 가 완전히 죽었다면
   #       환경변수 KILL_SWITCH_FALLBACK=global 을 Deployment 에 주고 rollout.
   kubectl -n agentoe set env deploy/backend-api KILL_SWITCH_FALLBACK=global
   kubectl -n agentoe rollout restart deploy/backend-api
   ```
   참고: [Runbook: Kill-switch 운영](./kill-switch-ops.md)
2. 이미 진행 중인 통화는 세션 FSM 이 in-memory 로 완주하도록 놓는다 — 이동 시키지 말 것.
3. 클라이언트(IVR 게이트웨이) 에 "잠시 후 다시 시도해 주세요" 메시지가 나가도록 이미 코딩됨.

## 복구 (15~60분)

1. Redis 기동/페일오버 확인:
   ```bash
   redis-cli -h <host> ping   # 반환 PONG 필요
   redis-cli -h <host> INFO replication | grep role
   ```
2. 키 무결성 점검:
   ```bash
   redis-cli --scan --pattern 'quota:*' | wc -l
   redis-cli --scan --pattern 'killswitch:*'
   ```
   Quota 카운터가 비어 있어도 OK — 당일 사용량은 누락되지만, 다음 정기 reconcile 에서 복구.
3. 백엔드 롤백:
   ```bash
   kubectl -n agentoe set env deploy/backend-api KILL_SWITCH_FALLBACK-  # 변수 제거
   kubectl -n agentoe rollout restart deploy/backend-api
   ```
4. `/api/v1/health` 가 `status: ok` 로 복귀하는지 확인 (2~3 분 대기).

## 사후 (Post-Incident)

* `agentoe_llm_tokens_consumed_total` 의 Redis 장애 구간에 대한 경과 기록.
* Quota 일일 총합을 CSV 로 export 하여 **수작업 billing 조정** 필요 여부 판단.
* 발생 원인이 메모리/CPU 포화라면, `resources.limits` 또는 인스턴스 size 조정 — ADR 신설 또는 티켓.
* 24 시간 내 블레임리스 포스트모템 작성.

## 관련

* [Runbook: Kill-switch 운영](./kill-switch-ops.md)
* [Runbook: DLQ 처리 절차](./runbook/dlq-processing.md)
* [ADR-002: 멀티테넌트 키 네임스페이스](../adr/ADR-002-tenant-key-namespace.md)
