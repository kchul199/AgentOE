# ──────────────────────────────────────────────────────────────────────────
# k8s-bootstrap — cluster 전역 컨트롤러 일괄 설치
# 변수 검증을 매 타깃 앞에 두지 않고 'guard' 한 번에 묶음.
# ──────────────────────────────────────────────────────────────────────────

REQUIRED_VARS := AGENTOE_CLUSTER_NAME AGENTOE_REGION ALB_ROLE_ARN EXT_DNS_ROLE_ARN ESO_ROLE_ARN
HELM := helm
KUBECTL := kubectl

# Helm 차트 버전 (재현성). 검증된 버전만 bump.
ALB_CONTROLLER_CHART_VERSION   ?= 1.7.2
CERT_MANAGER_CHART_VERSION     ?= v1.14.5
EXTERNAL_SECRETS_CHART_VERSION ?= 0.9.16
INGRESS_NGINX_CHART_VERSION    ?= 4.10.1
KARPENTER_VERSION              ?= 0.36.2
KPS_CHART_VERSION              ?= 60.4.0  # kube-prometheus-stack

.DEFAULT_GOAL := help

.PHONY: help
help:
	@echo "Targets:"
	@echo "  bootstrap        - 모든 컨트롤러 의존 순서대로 설치"
	@echo "  metrics-server   - metrics-server (HPA 전제조건)"
	@echo "  alb-controller   - AWS Load Balancer Controller"
	@echo "  cert-manager     - cert-manager + ClusterIssuer"
	@echo "  external-secrets - External Secrets Operator + ClusterSecretStore"
	@echo "  ingress-nginx    - (옵션) ingress-nginx"
	@echo "  karpenter        - Karpenter"
	@echo "  monitoring       - kube-prometheus-stack"
	@echo "  verify           - 설치 결과 점검"
	@echo "  uninstall-all    - 전부 제거 (조심)"

.PHONY: guard
guard:
	@for v in $(REQUIRED_VARS); do \
		if [ -z "$$(eval echo \$$$$v)" ]; then \
			echo "환경변수 $$v 가 비어있습니다."; exit 1; \
		fi; \
	done
	@echo "✓ env OK (cluster=$(AGENTOE_CLUSTER_NAME), region=$(AGENTOE_REGION))"

.PHONY: bootstrap
bootstrap: guard metrics-server alb-controller cert-manager external-secrets karpenter monitoring
	@echo "✓ bootstrap complete"

# ── metrics-server ────────────────────────────────────────────────────────
.PHONY: metrics-server
metrics-server:
	$(HELM) repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/ >/dev/null
	$(HELM) repo update metrics-server >/dev/null
	$(HELM) upgrade --install metrics-server metrics-server/metrics-server \
		-n kube-system \
		--set args[0]=--kubelet-insecure-tls \
		--wait

# ── AWS Load Balancer Controller ──────────────────────────────────────────
# IRSA: cluster_bootstrap.alb_controller_role_arn 을 ServiceAccount annotation 에.
.PHONY: alb-controller
alb-controller: guard
	$(HELM) repo add eks https://aws.github.io/eks-charts >/dev/null
	$(HELM) repo update eks >/dev/null
	$(KUBECTL) -n kube-system delete sa aws-load-balancer-controller --ignore-not-found
	$(HELM) upgrade --install aws-load-balancer-controller eks/aws-load-balancer-controller \
		-n kube-system \
		--version $(ALB_CONTROLLER_CHART_VERSION) \
		-f values/alb-controller.values.yaml \
		--set clusterName=$(AGENTOE_CLUSTER_NAME) \
		--set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=$(ALB_ROLE_ARN) \
		--wait

# ── cert-manager ──────────────────────────────────────────────────────────
.PHONY: cert-manager
cert-manager:
	$(HELM) repo add jetstack https://charts.jetstack.io >/dev/null
	$(HELM) repo update jetstack >/dev/null
	$(HELM) upgrade --install cert-manager jetstack/cert-manager \
		-n cert-manager --create-namespace \
		--version $(CERT_MANAGER_CHART_VERSION) \
		-f values/cert-manager.values.yaml \
		--wait
	$(KUBECTL) apply -f manifests/cluster-issuer-letsencrypt.yaml || true

# ── External Secrets Operator ─────────────────────────────────────────────
.PHONY: external-secrets
external-secrets: guard
	$(HELM) repo add external-secrets https://charts.external-secrets.io >/dev/null
	$(HELM) repo update external-secrets >/dev/null
	$(HELM) upgrade --install external-secrets external-secrets/external-secrets \
		-n external-secrets --create-namespace \
		--version $(EXTERNAL_SECRETS_CHART_VERSION) \
		-f values/external-secrets.values.yaml \
		--set serviceAccount.annotations."eks\.amazonaws\.com/role-arn"=$(ESO_ROLE_ARN) \
		--wait
	$(KUBECTL) apply -f manifests/cluster-secret-store-aws.yaml

# ── ingress-nginx (옵션) ─────────────────────────────────────────────────
# ALB → NGINX → Service 패턴 쓸 때만. ALB 만으로 충분하면 스킵.
.PHONY: ingress-nginx
ingress-nginx:
	$(HELM) repo add ingress-nginx https://kubernetes.github.io/ingress-nginx >/dev/null
	$(HELM) repo update ingress-nginx >/dev/null
	$(HELM) upgrade --install ingress-nginx ingress-nginx/ingress-nginx \
		-n ingress-nginx --create-namespace \
		--version $(INGRESS_NGINX_CHART_VERSION) \
		-f values/ingress-nginx.values.yaml \
		--wait

# ── Karpenter ─────────────────────────────────────────────────────────────
# IRSA / IAM Instance Profile / NodeClass 는 별도 Terraform 추가 필요.
# 본 타깃은 컨트롤러 helm 만 설치 — provisioner 는 manifests/ 에서.
.PHONY: karpenter
karpenter: guard
	$(HELM) registry logout public.ecr.aws 2>/dev/null || true
	$(HELM) upgrade --install karpenter \
		oci://public.ecr.aws/karpenter/karpenter \
		--version $(KARPENTER_VERSION) \
		-n karpenter --create-namespace \
		-f values/karpenter.values.yaml \
		--set settings.clusterName=$(AGENTOE_CLUSTER_NAME) \
		--wait

# ── 모니터링 (kube-prometheus-stack) ─────────────────────────────────────
.PHONY: monitoring
monitoring:
	$(HELM) repo add prometheus-community https://prometheus-community.github.io/helm-charts >/dev/null
	$(HELM) repo update prometheus-community >/dev/null
	$(HELM) upgrade --install kube-prometheus-stack prometheus-community/kube-prometheus-stack \
		-n monitoring --create-namespace \
		--version $(KPS_CHART_VERSION) \
		-f values/kube-prometheus-stack.values.yaml \
		--wait

# ── 검증 ──────────────────────────────────────────────────────────────────
.PHONY: verify
verify:
	$(KUBECTL) get deploy -A
	$(KUBECTL) get clusterissuer 2>/dev/null || true
	$(KUBECTL) get clustersecretstore 2>/dev/null || true
	$(KUBECTL) get servicemonitor -A 2>/dev/null | head || true

# ── 제거 (조심) ──────────────────────────────────────────────────────────
.PHONY: uninstall-all
uninstall-all:
	-$(HELM) -n monitoring uninstall kube-prometheus-stack
	-$(HELM) -n karpenter uninstall karpenter
	-$(HELM) -n ingress-nginx uninstall ingress-nginx
	-$(HELM) -n external-secrets uninstall external-secrets
	-$(HELM) -n cert-manager uninstall cert-manager
	-$(HELM) -n kube-system uninstall aws-load-balancer-controller
	-$(HELM) -n kube-system uninstall metrics-server
