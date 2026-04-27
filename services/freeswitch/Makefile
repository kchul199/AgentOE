# Makefile for vbgw-freeswitch
# Includes test runners, linter checks, and fuzz targets.

.PHONY: test test-race fuzz clean

# Standard tests (excluding long-running Fuzz tests)
test:
	@echo "Running Orchestrator and Bridge unit tests..."
	cd orchestrator && go test -v -short ./...
	cd bridge && go test -v -short ./...

# Tests with data race detector enabled
test-race:
	@echo "Running tests with race detector..."
	cd orchestrator && go test -v -race -short ./...
	cd bridge && go test -v -race -short ./...

# Fuzzing target: Specifically focuses on the MemoryStore capacity limit fuzz
fuzz:
	@echo "Running Fuzzing tests for 30 seconds..."
	cd orchestrator && go test -fuzz=FuzzMemoryStore -fuzztime=30s ./internal/session

clean:
	@echo "Cleaning up..."
	cd orchestrator && go clean
	cd bridge && go clean
	rm -f orchestrator/orchestrator bridge/bridge
