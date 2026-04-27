# Guide — Observability 운영 가이드

> 적용 범위: kube-prometheus-stack (Prometheus + Alertmanager + Grafana) + 자체 PrometheusRule × 3 + Grafana 대시보드 × 4 + Alertmanager 라우팅 (Slack + PagerDuty).

## 1. 어디에 무엇이 있나

| 영역             | 위치                                                     | 책임                                |
|------------------|----------------------------------------------------------|-------------------------------------|
| SLO 정의          | `docs/reference/slo.md`                                  | 단일 진실 소스                       |
| Recording rules   | `deploy/k8s-bootstrap/manifests/prometheus-rules/slo-recording.yaml` | 모든 SLI 비율 사전 계산 |
| Alerting rules    | `slo-alerting.yaml`, `infra-alerting.yaml`               | multi-window multi-burn-rate         |
| Alertmanager 설정 | `deploy/k8s-bootstrap/values/alertmanager.values.yaml`   | Slack/PagerDuty 라우팅              |
| AM secrets        | `manifests/alertmanager-receivers-externalsecret.yaml`   | ESO 가 Secrets Manager 에서 동기화  |
| Grafana JSON      | `deploy/observability/dashboards/*.json`                 | 4 개 대시보드                        |
| Dashboard kustomize | `deploy/observability/dashboards/kustomization.yaml`   | sidecar-loadable ConfigMap 생성      |
| 앱 메트릭 (backend) | `backend/app/core/metrics.py`, `middleware/http_metrics_middleware.py` | `/api/v1/metrics/prometheus` 노출 |
| 앱 메트릭 (vbgw)  | `vbgw/app/main.py`                                       | `/metrics` 노출                      |

## 2. 적용 순서 (zero → working observability)

`k8s-bootstrap/Makefile` 의 `monitoring` 타깃이 kube-prometheus-stack 을 띄우면 시작:

```bash
# 1) PrometheusRule 적용 — Prometheus Operator 가 자동 reload
kubectl apply -f deploy/k8s-bootstrap/manifests/prometheus-rules/

# 2) Alertmanager receivers 시크릿 (ENV 치환 필요)
ENV=staging envsubst < deploy/k8s-bootstrap/manifests/alertmanager-receivers-externalsecret.yaml \
  | kubectl apply -f -

# 3) Alertmanager helm 재배포 (라우팅 갱신)
helm upgrade kube-prometheus-stack prometheus-community/kube-prometheus-stack \
  -n monitoring \
  -f deploy/k8s-bootstrap/values/kube-prometheus-stack.values.yaml \
  -f deploy/k8s-bootstrap/values/alertmanager.values.yaml

# 4) Grafana 대시보드 ConfigMap (sidecar 가 자동 import)
kubectl apply -k deploy/observability/dashboards/
```

## 3. 적용 후 검증

```bash
# Prometheus Operator 가 rule 을 evaluation 중인지
kubectl -n monitoring port-forward svc/kube-prometheus-stack-prometheus 9090 &
open http://localhost:9090/rules     # 모든 그룹 OK 표시
open http://localhost:9090/alerts    # rule 등록 확인 (firing 0 이 정상)

# Alertmanager 라우팅 확인
kubectl -n monitoring port-forward svc/kube-prometheus-stack-alertmanager 9093 &
open http://localhost:9093/#/status  # config block 안의 receiver 목록 보임
# 라우팅 시뮬레이션 — alertmanager amtool
amtool config routes test --config.file=<(kubectl -n monitoring get secret alertmanager-kube-prometheus-stack-alertmanager -o jsonpath='{.data.alertmanager\.yaml}' | base64 -d) \
  severity=page service=backend

# Grafana 대시보드 자동 import 확인
kubectl -n monitoring port-forward svc/kube-prometheus-stack-grafana 3000 &
open http://localhost:3000   # 좌측 dashboards 메뉴 → AgentOE 4개 표시
```

## 4. 대시보드 사용

### 4.1 우선순위 매트릭스

| 상황                        | 첫 화면                          | 그 다음                            |
|-----------------------------|----------------------------------|------------------------------------|
| 페이지콜 받음                | `api-slo` 또는 `vbgw`           | 알람 메시지의 `runbook_url`        |
| "느린 것 같다" 사용자 리포트 | `agentic` (latency 패널)         | `infra` (CPU / memory)             |
| 배포 후 정상 검증            | `api-slo` 5m 윈도우, `agentic`   | release annotation 으로 시점 비교  |
| 월간 SLO 리뷰                | `api-slo` 30d 카드 + budget 게이지 | `slo:budget_remaining:*` 시리즈    |

### 4.2 카드 색깔 의미

- **녹색** — SLO 충족
- **주황** — 임계 근접 (다음 한 시간 안에 빨강 가능)
- **빨강** — SLO breach 또는 budget < 20%

### 4.3 변수

- `$datasource` — 멀티 클러스터 환경에서 prod / staging 전환
- `$tenant` (agentic 대시보드) — 특정 테넌트 한정
- `$namespace` (infra 대시보드) — agentoe / agentoe-staging 분리

## 5. 알람 → 대응 흐름

```text
PagerDuty 또는 Slack 알람
        │
        ▼
알람 메시지의 :book: runbook_url 클릭
        │
        ▼
 docs/runbook/alert-response-*.md 따라 진단 + 완화
        │
        ▼
 Slack #ops-incident 에 진행 상황 업데이트 (5분 간격)
        │
        ▼
 해결 후 — Alertmanager 가 자동 resolve
        │
        ▼
 사후 검토 — postmortem 필요 시
   docs/runbook/incident-response 절차로 진행
```

### 알람 silence 방법

```bash
# 임시 silence (예: 점검 30분간)
amtool silence add \
  --comment "scheduled maintenance ABCD-123" \
  --duration 30m \
  --alertmanager.url=http://localhost:9093 \
  alertname=APIErrorBudgetBurn_FastBurn

# 또는 Alertmanager UI 에서 클릭
```

> ⚠️ **silence 는 alert 를 해결하지 않는다 — 보지 않게 만들 뿐.** silence 만료 후에도 같은 상태면 자동 재발화.

## 6. SLO 변경 시 동기화

`docs/reference/slo.md` 의 임계값을 바꾸면 다음을 함께 업데이트해야 함:

1. **Recording rule 의 budget 분모** — 예 `(1 - 0.999)` → `(1 - 0.9995)`
2. **Alerting rule 의 임계** — `(1 - 0.999) * 14.4` 같은 모든 곱셈
3. **Grafana 대시보드의 임계 색상** — `thresholds.steps` 의 value
4. **이 문서** — 변경 이력 섹션

PR 1 개에 4 개 변경이 모두 포함되어야 머지 — `validate.yml` 의 helm-lint + kubeconform 이 rule 문법은 잡지만 임계값 일관성은 휴먼 리뷰.

## 7. 메트릭 추가 / 확장

새 SLI 가 필요하면 다음 4 단계:

1. 앱에 metric 노출 (`metrics.py` 또는 `http_metrics_middleware.py`)
2. `slo-recording.yaml` 에 ratio 시리즈 추가 (5m/1h/6h/30d)
3. `slo.md` 에 SLI 정의 + SLO target 명시
4. Grafana 대시보드 패널 + alerting rule 추가

> 라벨 카디널리티는 항상 **유한 + 알려진 집합** 으로 제한한다 — `tenant` 처럼 무한 가능한 라벨은 별도 검토.

## 8. 비용 / 성능 메모

- **Prometheus disk**: kube-prometheus-stack 기본 retention 7d, gp3 30Gi (kps values). 30d SLO 윈도우는 recording rule 이 미리 합산된 값을 보존하므로 raw retention 이 짧아도 OK.
- **Recording rule cost**: 30s 간격 × 60 시리즈 × 7 윈도우 ≈ 무시 가능 수준. SLO 시리즈 하나당 수백 KB/일.
- **Alertmanager replicas 2** — gossip 으로 silence/notification 상태 동기. 단일 leader 없음.
- **Grafana dashboards**: sidecar 자동 import — ConfigMap 변경 시 30s 안에 갱신.

## 9. 흔한 함정

| 증상                                         | 원인                                           | 해결                                              |
|---------------------------------------------|------------------------------------------------|---------------------------------------------------|
| Recording rule 결과가 NaN                   | 분모 0 (해당 시간대 트래픽 없음)               | `clamp_min(..., 0.001)` 으로 보호 (이미 적용)     |
| burn rate 알람이 트래픽 없는 시간대에 raise   | 5m 윈도우에 단 1건 5xx → 100% 실패율           | `for: 2m` + 두 윈도우 AND — 이미 noise 줄였음     |
| Alertmanager 가 PagerDuty 안 보냄            | routing_key_file 빈 값 또는 경로 오타          | `kubectl exec -it alertmanager-* -- cat` 으로 확인 |
| Grafana 대시보드 안 보임                    | ConfigMap 라벨 `grafana_dashboard=1` 누락      | kustomization 의 generatorOptions.labels 확인     |
| alert 는 firing 인데 Slack 안 옴             | mute_time_intervals 'kst-night' 적용된 시간대 | 의도된 동작 — page severity 는 야간에도 발화      |
| histogram_quantile 결과가 들쭉날쭉           | bucket 수가 적음 + 트래픽 부족                  | bucket 추가 또는 longer window (`[15m]`)          |

## 10. 다음 개선 후보

- Tempo / Jaeger 분산 트레이스 — 현재는 trace_id 만 로그에 박혀 있음 (samplerate=0)
- Loki — 구조화 로그를 Prometheus alert 와 같은 라벨로 조회
- Grafana annotation API — 배포 / 인시던트 timeline 을 모든 대시보드에 자동 표시
- Sloth — SLO YAML → PrometheusRule 자동 생성 (현재는 수기)
- SLO error budget Slack 봇 — 매일 아침 budget 잔량 broadcast
