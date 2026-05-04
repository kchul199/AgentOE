# DEPRECATED — agentoe-vbgw Helm chart

> ⚠️ **이 chart 는 더 이상 production 에 배포하지 않습니다.**
> 실제 vbgw 는 별도 프로젝트의 자체 chart 사용.

## 왜 deprecate?

- vbgw 는 별도 프로젝트 `vbgw_v2/` 가 owner.
- vbgw_v2 의 실제 구조는 단일 Python 서비스가 아니라 **3 컴포넌트** (orchestrator / bridge / freeswitch).
- vbgw_v2 가 자체 Helm chart (`vbgw_v2/charts/vbgw/`) 를 보유.
- 이 chart 는 우리 `skeleton/vbgw/` Python placeholder 기준 — 실 구현과 어긋남.

## 무엇으로 대체?

| 우리 chart 가 했던 것                | 새 위치                                                |
|-------------------------------------|--------------------------------------------------------|
| vbgw deployment                     | `vbgw_v2/charts/vbgw/templates/deployment-{bridge,orchestrator,freeswitch}.yaml` |
| values.yaml                         | `vbgw_v2/charts/vbgw/values.yaml`                      |
| ServiceMonitor                      | (vbgw_v2 에 추가 필요 — 향후 PR)                        |
| ExternalSecret 시크릿 동기화         | (vbgw_v2 에 추가 필요 — 향후 PR)                        |

## 그래도 왜 안 지우나

1. **Reference**: vbgw_v2 가 우리 SLO / IRSA / ESO 패턴을 따라가야 함. 이 chart 가 가장 가까운 reference.
2. **Test stub deployment**: dev 환경에서 `skeleton/vbgw/` Python stub 을 띄울 일이 있을 때 사용 가능.
3. **삭제 위험**: deploy-staging.yml 등에서 참조 — 안전하게 비활성화 후 추후 정리.

## CI/CD 영향

- `validate.yml` 의 helm-lint 매트릭스에서 `agentoe-vbgw` 는 계속 lint 됨 (drift 없게).
- `deploy-staging.yml` / `deploy-production.yml` 의 vbgw 매트릭스 entry 는 **다음 PR 에서 제거 예정**.
- `build-images.yml` 의 vbgw 이미지 빌드도 함께 제거 (vbgw_v2 가 자체 push).

## 관련 문서

- Cross-project integration: `skeleton/docs/guide/cross-project-integration.md`
- Real vbgw 위치: `vbgw_v2/` 또는 host `/Users/kchul199/vbgw_v2/`
