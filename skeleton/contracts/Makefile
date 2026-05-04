# ──────────────────────────────────────────────────────────────────────────
# AgenticOE_v2 / contracts — proto canonical management
#
# 사용:
#   make gen-python       Python stub 생성 (backend 가 import)
#   make gen-go           Go stub 생성 (vbgw 동기화 전 검증용)
#   make gen              둘 다
#   make sync-vbgw VBGW=/Users/x/vbgw_v2   vbgw_v2 의 3 곳에 .proto + go stub 동기화
#   make verify-vbgw VBGW=...              vbgw 의 proto 가 우리 canonical 과 동일한지 검증
#   make clean            gen/* 삭제
#
# 의존: protoc, grpc_tools (Python), protoc-gen-go + protoc-gen-go-grpc (Go)
# ──────────────────────────────────────────────────────────────────────────

PROTO_DIR    := proto
GEN_PY       := gen/python
GEN_GO       := gen/go
SCRIPTS      := scripts

VBGW         ?= /Users/kchul199/vbgw_v2

.PHONY: all gen gen-python gen-go sync-vbgw verify-vbgw clean tools-check

all: gen

# ── 의존성 점검 ────────────────────────────────────────────────────────
tools-check:
	@command -v protoc >/dev/null 2>&1 || { echo "protoc 미설치"; exit 1; }
	@python3 -c "import grpc_tools.protoc" 2>/dev/null \
	  || { echo "pip install --break-system-packages grpcio-tools"; exit 1; }
	@command -v protoc-gen-go >/dev/null 2>&1 \
	  || { echo "go install google.golang.org/protobuf/cmd/protoc-gen-go@latest"; exit 1; }
	@command -v protoc-gen-go-grpc >/dev/null 2>&1 \
	  || { echo "go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest"; exit 1; }

# ── Python stub ────────────────────────────────────────────────────────
gen-python:
	@mkdir -p $(GEN_PY)/voicebot
	python3 -m grpc_tools.protoc \
		-I$(PROTO_DIR) \
		--python_out=$(GEN_PY) \
		--pyi_out=$(GEN_PY) \
		--grpc_python_out=$(GEN_PY) \
		$(PROTO_DIR)/voicebot.proto
	@touch $(GEN_PY)/__init__.py
	@echo "[OK] Python stub → $(GEN_PY)/"

# ── Go stub ────────────────────────────────────────────────────────────
gen-go:
	@mkdir -p $(GEN_GO)/voicebot
	protoc \
		-I$(PROTO_DIR) \
		--go_out=$(GEN_GO) --go_opt=paths=source_relative \
		--go-grpc_out=$(GEN_GO) --go-grpc_opt=paths=source_relative \
		$(PROTO_DIR)/voicebot.proto
	@echo "[OK] Go stub → $(GEN_GO)/"

gen: gen-python gen-go

# ── vbgw 동기화 ───────────────────────────────────────────────────────
sync-vbgw:
	@test -d "$(VBGW)" || { echo "VBGW=$(VBGW) 디렉토리 없음"; exit 1; }
	@./$(SCRIPTS)/sync-to-vbgw.sh "$(VBGW)"

verify-vbgw:
	@test -d "$(VBGW)" || { echo "VBGW=$(VBGW) 디렉토리 없음"; exit 1; }
	@./$(SCRIPTS)/verify-vbgw-proto.sh "$(VBGW)"

clean:
	rm -rf gen/python/voicebot* gen/python/__init__.py gen/go/voicebot
	@echo "[OK] cleaned"
