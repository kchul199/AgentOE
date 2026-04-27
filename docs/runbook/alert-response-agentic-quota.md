# Runbook — `LLMQuotaRejectsBurst` & `AgenticPipelineFailureBurn_*`

> 트리거 alert: `LLMQuotaRejectsBurst` (ticket), `AgenticPipelineFailureBurn_FastBurn` (page), `AgenticPipelineFailureBurn_Ticket`
> 대시보드: [Agentic](https://grafana.agentoe.io/d/agentic)

이 runbook 은 두 가지 시나리오를 다룬다:

1. **테넌트가 일일 quota 를 초과해서 `result=reject` 가 polled** — `LLMQuotaRejectsBurst`
2. **Agentic 파이프라인 자체가 실패** — `AgenticPipelineFailureBurn_*`

대응이 다르므로 분기 판단이 첫 단계.

## 0. 첫 5분

대시보드의 "LLM quota check outcomes" 패널을 본다:

| 화면                                              | 상황                                  | 점프 |
|---------------------------------------------------|---------------------------------------|------|
| `result=reject` 가 spike, 그 외는 정상            | 특정 테넌트가 한도 초과               | 1.A  |
| `result=fallback` 비율 ↑                          | quota 폴리시 'fallback' 작동 — 정상 동작이지만 모델 다운그레이드 발생 중 | 1.B |
| pipeline error rate ↑ + quota 정상                | LLM/STT/TTS 자체 장애                 | 2    |

## 1. 시나리오 A — Quota 초과

### 1.A 어느 테넌트?
```promql
topk(5,
  sum by (tenant) (rate(agentoe_llm_quota_checks_total{result="reject"}[15m]))
)
```
Grafana → "Top tenants by LLM cost (1h)" 패널과 교차 확인.

### 1.B 의도된 초과 vs 비정상 사용 패턴
- 마케팅 캠페인 / 신규 large account onboarding 으로 예상되는가? → **계획된 over-usage** → 8.A (quota 임시 상향)
- 동일 테넌트의 평소 사용량 대비 5× 이상 spike + abuse 의심 → 8.B (quota 즉시 차단)
- 새 codepath 가 token 을 비효율적으로 쓰고 있나? → 8.C (PR 시급)

## 2. 시나리오 B — Pipeline 실패

### 2.1 어느 단계에서?
대시보드 "STT / LLM / TTS p95 latency" + "Circuit breaker state" 동시 확인:

| CB OPEN 인 service | 의미                  | 점프      |
|--------------------|-----------------------|-----------|
| `groq_stt`         | Groq STT 장애         | 8.D       |
| `groq_llm`         | Groq LLM 장애         | 8.D + 8.F |
| `google_tts`       | Google TTS 장애       | 8.E       |
| `bedrock_llm`      | 폴백마저 OPEN         | 9.G — full degraded |

### 2.2 코드 예외 패턴
```bash
kubectl -n agentoe logs -l app.kubernetes.io/name=agentoe-backend \
  --since=15m --tail=2000 \
  | jq -r 'select(.event=="agentic_pipeline_error") | .stage + " " + (.error // .exception // "")' \
  | sort | uniq -c | sort -nr
```

### 2.3 Idempotency 폭주
드물지만 retry storm 이 있으면 같은 idempotency key 가 반복 → quota 폭주처럼 보일 수 있음.
```promql
sum(rate(agentoe_idempotency_replays_total[5m]))
```

## 3. 종료 기준

- Quota: `agentoe_llm_quota_checks_total{result="reject"}` rate ≤ 0.1/s 가 15분
- Pipeline: `slo:agentic_success_ratio:rate5m` ≥ 99.5% 가 15분

## 4. 사후

- Quota 초과는 **CSM 에 통보** (테넌트 계정 담당자) — 우리가 임시 상향했다면 영업이 후속 협의.
- Pipeline 장애는 외부 의존성 SLA 위반 시 vendor escalation.

## 5. 관련 문서

- `docs/runbook/llm-quota-exceeded.md` — quota 폴리시 깊이
- `docs/runbook/kill-switch-ops.md` — degraded mode 전환
- SLO 정의: `docs/reference/slo.md`

---

## 8. 완화 액션 (Cookbook)

### 8.A — 테넌트 quota 임시 상향
- 운영 DB 의 tenant settings 직접 수정 (또는 admin API)
- 사고 종료 후 영구 변경 여부는 영업/CSM 결정

### 8.B — 테넌트 즉시 차단 (악성/abuse)
```bash
# kill-switch 의 tenant blacklist 활용
curl -X POST http://localhost:8000/admin/v1/tenants/$TID/disable \
  -H "Authorization: Bearer $ADMIN_TOKEN"
```

### 8.C — Token 비효율 hot-fix
- 평균 prompt token 이 평소 대비 ↑ → 새 코드의 컨텍스트 window 가 폭발했나?
- 임시: `MAX_CONTEXT_TOKENS` 환경변수 하향
  ```bash
  kubectl -n agentoe set env deploy/agentoe-backend MAX_CONTEXT_TOKENS=2048
  ```

### 8.D — Groq → Bedrock 폴백 강제
- Circuit breaker 가 자동으로 OPEN 시키지 못한 경우 (transient 5xx 산발) 수동 강제:
  ```bash
  kubectl -n agentoe set env deploy/agentoe-backend GROQ_FORCE_DISABLED=true
  ```
  (이 env 가 활성화되면 router 가 즉시 fallback chain 으로 전환)

### 8.E — Google TTS → 폴백 음성 (캐시된 wav)
- `kill-switch` runbook 의 `degraded_voice` 모드:
  ```bash
  kubectl -n agentoe set env deploy/agentoe-backend KILL_SWITCH_DEGRADED_VOICE=true
  ```

### 8.F — LLM 폴백 모델 강제
- Llama-3.1-70B 가 OPEN 이면 Llama-3.1-8B 로 강제:
  ```bash
  kubectl -n agentoe set env deploy/agentoe-backend FORCE_LLM_MODEL=llama-3.1-8b-instant
  ```

### 8.G — Full degraded (모든 LLM 장애)
- `AGENTIC_DISABLED=true` 로 룰 기반 응답 폴백
  ```bash
  kubectl -n agentoe set env deploy/agentoe-backend AGENTIC_DISABLED=true
  ```
- 이 경우 사용자에게 응답 품질 저하가 명확히 보임 — Status page 에 incident 게시.
