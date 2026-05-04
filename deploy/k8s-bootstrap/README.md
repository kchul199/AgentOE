# k8s-bootstrap

EKS 클러스터가 올라온 직후 한 번에 설치해야 하는 cluster-wide 컨트롤러 묶음입니다.
Argo CD 같은 GitOps 가 들어오면 Application 으로 옮기지만, 그 전 cold-start 단계에서는
이 디렉토리의 Helmfile/Makefile 만으로 설치할 수 있게 구성했습니다.

## 설치 순서 (의존성)

| # | 컴포넌트 | 이유 / 의존 |
| --- | --- | --- |
| 1 | metrics-server | HPA 가 작동하려면 가장 먼저 |
| 2 | aws-load-balancer-controller | Ingress=alb 처리 (IRSA 필요) |
| 3 | cert-manager | TLS 인증서 발급 (외부 ACM 또는 Let's Encrypt) |
| 4 | external-secrets-operator | Secrets Manager → K8s Secret 동기화 |
| 5 | ingress-nginx | (옵션) ALB → NGINX → Service 2단 구조용 |
| 6 | karpenter | 동적 노드 프로비저닝 (system NG 외 워크로드용) |
| 7 | kube-prometheus-stack | 모니터링 스택 |

## 사전 조건

- Terraform (`environments/staging`) apply 완료 → `cluster_bootstrap` 모듈이 IRSA role 들을 만들어 둠.
- 다음 ARN 을 환경변수로 export:
  ```sh
  export AGENTOE_CLUSTER_NAME=$(terraform output -raw cluster_name)
  export AGENTOE_REGION=ap-northeast-2
  export ALB_ROLE_ARN=$(terraform output -raw irsa_alb_controller)
  export EXT_DNS_ROLE_ARN=$(terraform output -raw irsa_external_dns)
  export ESO_ROLE_ARN=$(terraform output -raw irsa_external_secrets)
  ```

## 한 번에 설치

```sh
make bootstrap
```

내부적으로는 `helm upgrade --install` 들을 의존 순서대로 실행합니다.

## 개별 설치 / 갱신

```sh
make alb-controller       # AWS Load Balancer Controller
make cert-manager
make external-secrets
make ingress-nginx
make karpenter
make monitoring           # kube-prometheus-stack
```

## 검증

```sh
make verify
# - kubectl get deployment -A → 모두 READY
# - kubectl get crd | grep cert-manager → Issuer/Certificate 등록
# - kubectl get clusterissuer → letsencrypt-prod / staging-acm Ready
# - kubectl get clustersecretstore → aws-secrets-manager Ready
```
