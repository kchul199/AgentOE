# Runbook — `VBGWCallSetupFailure_FastBurn` / `VBGWMidCallDrops_FastBurn`

> 트리거 alert 두 종류:
> - **VBGWCallSetupFailure_FastBurn** — 통화가 아예 연결되지 않음
> - **VBGWMidCallDrops_FastBurn** — 통화 중 끊김
>
> 대시보드: [VBGW](https://grafana.agentoe.io/d/vbgw)
> 둘 다 page severity — 고객이 콜봇과 대화를 못 함 = 사업 직격탄.

## 0. 첫 5분

1. Slack `#ops-incident` thread 시작 (severity 명시).
2. VBGW 대시보드:
   - **Setup ratio** 패널이 빨강 → `setup_fail` (8.A 먼저 확인)
   - **Drop ratio** 패널이 빨강 → `mid_call_drop` (8.B 먼저 확인)
   - 둘 다 빨강 → 8.A + 8.B 병행 (대개 같은 원인)

## 1. 진단

### 1.1 setup 실패 — fail 원인
```bash
kubectl -n agentoe logs -l app.kubernetes.io/name=agentoe-vbgw \
  --since=10m --tail=500 \
  | jq -r 'select(.event == "call_setup_fail") | .reason' \
  | sort | uniq -c | sort -nr
```

| reason 빈도 1위           | 의미                                        | 점프 |
|---------------------------|---------------------------------------------|------|
| `codec_negotiation_failed`| SIP/WebRTC 클라가 지원 안 하는 codec 요구    | 8.C  |
| `auth_failed`             | JWT 검증 실패 (JWKS / clock skew)           | 8.D  |
| `backend_unreachable`     | backend WS 도달 실패 — backend 자체 문제    | API SLO runbook |
| `rate_limit`              | tenant 한도 초과                            | 8.E  |

### 1.2 mid-call drop — termination 분포
VBGW 대시보드의 "Termination reasons" 패널 — 어느 reason 이 spike?

| reason       | 의미                                          | 점프 |
|--------------|-----------------------------------------------|------|
| `network`    | 클라이언트 측 네트워크 (대부분 일시적)          | 모니터만 — 1회로 끝나면 무시 |
| `server_error`| backend 또는 vbgw 코드 예외                  | 1.3 logs |
| `crash`      | Pod OOM / panic                               | 1.4 pods |
| `timeout`    | RTP keepalive 미수신                          | 8.F  |

### 1.3 코드 예외 추적
```bash
kubectl -n agentoe logs -l app.kubernetes.io/name=agentoe-vbgw \
  --since=15m --tail=1000 \
  | jq -r 'select(.level=="ERROR") | "\(.timestamp) \(.event // "n/a") \(.error // .exception // "")"' \
  | sort | uniq -c | sort -nr | head -10
```

### 1.4 OOM / restart
```bash
kubectl -n agentoe describe pod -l app.kubernetes.io/name=agentoe-vbgw \
  | grep -A2 -E 'Last State|OOMKilled|Restart Count'
```
restart count > 0 + reason `OOMKilled` → 8.G (resources 늘리고 max_concurrent_calls 낮춤).

### 1.5 외부 SIP 트렁크 (있다면)
NLB 의 access log 확인 — 5xx / connection reset 이 SIP 게이트웨이 측 문제인지.

## 2. 완화 결정 트리

```text
직전 vbgw 배포가 원인?
├── YES → 8.H (rollback)
└── NO
    ├── OOM 다발? → 8.G (resources/limit)
    ├── auth_failed 다발? → 8.D (JWKS)
    ├── codec 협상 실패 다발? → 8.C (코덱 설정 hot-fix)
    └── 그 외 → 임시 트래픽 차단 (NLB target group drain) + 사후 hot-fix
```

## 3. 종료 기준
- `slo:vbgw_call_setup_ratio:rate5m` ≥ 99.9% 가 15분 유지
- `slo:vbgw_mid_call_drop_ratio:rate5m` ≤ 0.1% 가 15분 유지
- Slack thread 에 root cause + 완화 액션 문서화

## 4. 사후
- **항상 postmortem.** 통화 끊김은 고객 신뢰 직접 손실.
- vbgw 로직이 실제 음성 처리로 확장되면 (Phase 2-G 의 placeholder 이상),
  본 runbook 의 reason 라벨 목록도 같이 확장해야 함.

---

## 8. 완화 액션 (Cookbook)

### 8.A — Setup fail (모든 통화) 차단
```bash
# 외부 NLB 에서 traffic 일시 차단 — 새 통화 거부, 기존 통화는 보존
kubectl -n agentoe annotate svc agentoe-vbgw-external \
  service.beta.kubernetes.io/aws-load-balancer-attributes-
# ↑ idle_timeout 만 살리고 attributes 제거 → SIP signaling 게이트웨이가 해석할 수 없게 만듬
# (실제 운영에선 NLB target group 의 deregistration 이 더 안전)
```

### 8.B — Mid-call drop 발생 중 — 보존 가능한 통화는 보존
- Helm rollback 시 `--atomic` 으로 새 Pod 가 안 뜨면 자동 복구
- 새 Pod 가 trigger 한 drop 이면 신규 트래픽만 막고 기존 Pod 보존:
  ```bash
  kubectl -n agentoe scale deploy/agentoe-vbgw --replicas=$(kubectl -n agentoe get deploy agentoe-vbgw -o jsonpath='{.status.readyReplicas}')
  ```

### 8.C — 코덱 협상 실패
- vbgw config 에서 fallback codec 추가:
  ```bash
  kubectl -n agentoe set env deploy/agentoe-vbgw \
    SUPPORTED_CODECS='opus,g711a,g711u,g729'
  ```
- 그 후 PR 로 영구 수정.

### 8.D — JWT/JWKS 검증 실패
- API SLO runbook 의 8.E 참고. vbgw 도 동일 캐시 사용.

### 8.E — Rate-limit 초과
- 임시 한도 상향 (사고 동안만):
  ```bash
  kubectl -n agentoe set env deploy/agentoe-vbgw MAX_CONCURRENT_CALLS=80
  ```
- 사고 종료 후 원복.

### 8.F — RTP keepalive timeout
- `idle_timeout.timeout_seconds` 가 클라이언트 측 keepalive 주기보다 짧을 때 발생.
- NLB 어노테이션 임시 상향 (350 → 600):
  ```bash
  kubectl -n agentoe edit svc agentoe-vbgw-external
  # service.beta.kubernetes.io/aws-load-balancer-attributes 의 idle_timeout 수정
  ```

### 8.G — OOM 다발
```bash
kubectl -n agentoe set resources deploy/agentoe-vbgw \
  --containers=vbgw \
  --requests=cpu=2,memory=3Gi \
  --limits=cpu=6,memory=6Gi
# MAX_CONCURRENT_CALLS 도 함께 하향해서 단일 Pod 부담 감소
kubectl -n agentoe set env deploy/agentoe-vbgw MAX_CONCURRENT_CALLS=20
```

### 8.H — 직전 vbgw 배포 롤백
```bash
helm -n agentoe history agentoe-vbgw
helm -n agentoe rollback agentoe-vbgw <REV>
kubectl -n agentoe rollout status deploy/agentoe-vbgw
```
