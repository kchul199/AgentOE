# ──────────────────────────────────────────────────────────────────────────
# agentoe Helm 배포 헬퍼
#
# 사용 예:
#   ENV=staging make lint
#   ENV=staging make deploy-backend
#   ENV=staging make deploy-all
#   ENV=prod    make diff-backend          # helm-diff plugin 필요
#
# 전제:
#   - kubectl context 가 대상 클러스터로 설정되어 있을 것
#   - REPLACE_* 토큰은 envsubst 또는 helm --set 으로 미리 채울 것
# ──────────────────────────────────────────────────────────────────────────

ENV ?= staging
NAMESPACE ?= agentoe-$(ENV)
ifeq ($(ENV),prod)
  NAMESPACE := agentoe
endif

VALUES_DIR := values/$(ENV)
SHARED_DIR := values/shared

HELM ?= helm
KUBECTL ?= kubectl

# ── 검증 ────────────────────────────────────────────────────────────────
.PHONY: lint
lint:
	$(HELM) lint ./agentoe-backend  -f $(VALUES_DIR)/backend.values.yaml
	$(HELM) lint ./agentoe-vbgw     -f $(VALUES_DIR)/vbgw.values.yaml
	$(HELM) lint ./agentoe-frontend -f $(VALUES_DIR)/frontend.values.yaml

.PHONY: template
template:
	$(HELM) template agentoe-backend  ./agentoe-backend  -f $(VALUES_DIR)/backend.values.yaml  -n $(NAMESPACE)
	$(HELM) template agentoe-vbgw     ./agentoe-vbgw     -f $(VALUES_DIR)/vbgw.values.yaml     -n $(NAMESPACE)
	$(HELM) template agentoe-frontend ./agentoe-frontend -f $(VALUES_DIR)/frontend.values.yaml -n $(NAMESPACE)

# ── 클러스터 공통 (PriorityClass 등) ──────────────────────────────────
.PHONY: shared
shared:
	$(KUBECTL) apply -f $(SHARED_DIR)/priorityclasses.yaml

.PHONY: namespace
namespace:
	$(KUBECTL) get ns $(NAMESPACE) >/dev/null 2>&1 || $(KUBECTL) create ns $(NAMESPACE)
	$(KUBECTL) label ns $(NAMESPACE) name=$(NAMESPACE) --overwrite

# ── 개별 배포 ───────────────────────────────────────────────────────────
.PHONY: deploy-backend
deploy-backend: namespace
	$(HELM) upgrade --install agentoe-backend ./agentoe-backend \
		-n $(NAMESPACE) \
		-f $(VALUES_DIR)/backend.values.yaml \
		--wait --timeout 5m

.PHONY: deploy-vbgw
deploy-vbgw: namespace
	$(HELM) upgrade --install agentoe-vbgw ./agentoe-vbgw \
		-n $(NAMESPACE) \
		-f $(VALUES_DIR)/vbgw.values.yaml \
		--wait --timeout 5m

.PHONY: deploy-frontend
deploy-frontend: namespace
	$(HELM) upgrade --install agentoe-frontend ./agentoe-frontend \
		-n $(NAMESPACE) \
		-f $(VALUES_DIR)/frontend.values.yaml \
		--wait --timeout 3m

.PHONY: deploy-all
deploy-all: shared deploy-backend deploy-vbgw deploy-frontend

# ── diff (helm-diff plugin) ─────────────────────────────────────────────
.PHONY: diff-backend
diff-backend:
	$(HELM) diff upgrade agentoe-backend ./agentoe-backend \
		-n $(NAMESPACE) \
		-f $(VALUES_DIR)/backend.values.yaml

.PHONY: diff-vbgw
diff-vbgw:
	$(HELM) diff upgrade agentoe-vbgw ./agentoe-vbgw \
		-n $(NAMESPACE) \
		-f $(VALUES_DIR)/vbgw.values.yaml

.PHONY: diff-frontend
diff-frontend:
	$(HELM) diff upgrade agentoe-frontend ./agentoe-frontend \
		-n $(NAMESPACE) \
		-f $(VALUES_DIR)/frontend.values.yaml

# ── 롤백 ────────────────────────────────────────────────────────────────
.PHONY: rollback-backend
rollback-backend:
	$(HELM) rollback agentoe-backend -n $(NAMESPACE)

.PHONY: rollback-vbgw
rollback-vbgw:
	$(HELM) rollback agentoe-vbgw -n $(NAMESPACE)

.PHONY: rollback-frontend
rollback-frontend:
	$(HELM) rollback agentoe-frontend -n $(NAMESPACE)

# ── 상태 ────────────────────────────────────────────────────────────────
.PHONY: status
status:
	$(HELM) list -n $(NAMESPACE)
	$(KUBECTL) -n $(NAMESPACE) get pods,svc,ingress,hpa,pdb

.PHONY: uninstall
uninstall:
	-$(HELM) uninstall agentoe-frontend -n $(NAMESPACE)
	-$(HELM) uninstall agentoe-vbgw     -n $(NAMESPACE)
	-$(HELM) uninstall agentoe-backend  -n $(NAMESPACE)

# ── ENV 토큰 자동 치환 (terraform output → values) ────────────────────
# CI 에서 다음과 같이 사용:
#   terraform -chdir=../terraform/environments/$(ENV) output -json > /tmp/tf.json
#   ENV=$(ENV) ./scripts/render-values.sh /tmp/tf.json
.PHONY: render-values
render-values:
	@./scripts/render-values.sh $(ENV)
