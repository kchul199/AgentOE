#!/usr/bin/env bash
# run_load_test.sh — Phase 3 부하 테스트 (100 동시콜 30분)
# 변경이력: v1.0.0 | 2026-04-07 | [Implementer] | Phase 3 | SIPp + 모니터링
#
# Usage: ./scripts/run_load_test.sh [TARGET_IP] [DURATION_SEC] [CONCURRENT]
#
# Prerequisites:
#   - SIPp installed (apt install sipp / brew install sipp)
#   - Docker Compose stack running (docker compose up -d)
#   - Mock AI server running (cd src/emulator && python mock_server.py)

set -euo pipefail

TARGET_IP="${1:-127.0.0.1}"
DURATION_MS="${2:-300000}"       # 5 minutes per call (default)
CONCURRENT="${3:-100}"           # 100 concurrent calls
TOTAL_CALLS="${4:-600}"          # 600 total = 30 min cycle
RATE="${5:-2}"                   # 2 calls per second

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/../results/load_$(date +%Y%m%d_%H%M%S)"
SIPP_SCENARIO="${SCRIPT_DIR}/../tests/sipp/load_test_uac.xml"

mkdir -p "${RESULTS_DIR}"

echo "============================================"
echo " Phase 3 Load Test"
echo "============================================"
echo " Target:     ${TARGET_IP}:5060"
echo " Duration:   ${DURATION_MS}ms per call"
echo " Concurrent: ${CONCURRENT}"
echo " Total:      ${TOTAL_CALLS} calls"
echo " Rate:       ${RATE} calls/sec"
echo " Results:    ${RESULTS_DIR}"
echo "============================================"

# Pre-test: capture baseline metrics
echo "[1/5] Capturing baseline metrics..."
curl -s "http://${TARGET_IP}:8080/metrics" > "${RESULTS_DIR}/metrics_before.txt" 2>/dev/null || true
curl -s "http://${TARGET_IP}:8080/health" > "${RESULTS_DIR}/health_before.json" 2>/dev/null || true

# Pre-test: capture baseline heap
echo "[2/5] Capturing baseline heap profile..."
curl -s "http://${TARGET_IP}:8080/debug/pprof/heap" > "${RESULTS_DIR}/heap_before.prof" 2>/dev/null || true

# Start SLO monitor in background
echo "[3/5] Starting SLO monitor..."
"${SCRIPT_DIR}/slo_monitor.sh" "${TARGET_IP}" "${RESULTS_DIR}/slo_log.csv" &
SLO_PID=$!

# Run SIPp load test
echo "[4/5] Starting SIPp load test..."
sipp -sf "${SIPP_SCENARIO}" \
    -d "${DURATION_MS}" \
    -l "${CONCURRENT}" \
    -m "${TOTAL_CALLS}" \
    -r "${RATE}" -rp 1000 \
    -trace_stat -stf "${RESULTS_DIR}/sipp_stats.csv" \
    -trace_err -ef "${RESULTS_DIR}/sipp_errors.log" \
    -trace_screen -screen_file "${RESULTS_DIR}/sipp_screen.log" \
    -timeout 600 \
    "${TARGET_IP}:5060" \
    2>&1 | tee "${RESULTS_DIR}/sipp_output.log"

SIPP_EXIT=$?

# Stop SLO monitor
kill "${SLO_PID}" 2>/dev/null || true

# Post-test: capture final metrics
echo "[5/5] Capturing post-test metrics..."
curl -s "http://${TARGET_IP}:8080/metrics" > "${RESULTS_DIR}/metrics_after.txt" 2>/dev/null || true
curl -s "http://${TARGET_IP}:8080/health" > "${RESULTS_DIR}/health_after.json" 2>/dev/null || true
curl -s "http://${TARGET_IP}:8080/debug/pprof/heap" > "${RESULTS_DIR}/heap_after.prof" 2>/dev/null || true

# Generate report
echo ""
echo "============================================"
echo " Load Test Results"
echo "============================================"

echo ""
echo "--- SIPp Exit Code: ${SIPP_EXIT} ---"
if [ "${SIPP_EXIT}" -eq 0 ]; then
    echo "  PASS: All calls completed successfully"
else
    echo "  FAIL: SIPp reported errors (check ${RESULTS_DIR}/sipp_errors.log)"
fi

echo ""
echo "--- SLO Metrics (post-test) ---"
echo "Active calls:"
grep "vbgw_active_calls" "${RESULTS_DIR}/metrics_after.txt" 2>/dev/null || echo "  (unavailable)"

echo "Dropped frames:"
grep "vbgw_grpc_dropped_frames_total" "${RESULTS_DIR}/metrics_after.txt" 2>/dev/null || echo "  0"

echo "Stream errors:"
grep "vbgw_grpc_stream_errors_total" "${RESULTS_DIR}/metrics_after.txt" 2>/dev/null || echo "  0"

echo "Barge-in events:"
grep "vbgw_bargein_events_total" "${RESULTS_DIR}/metrics_after.txt" 2>/dev/null || echo "  0"

echo ""
echo "--- Heap Profile ---"
echo "Before: ${RESULTS_DIR}/heap_before.prof"
echo "After:  ${RESULTS_DIR}/heap_after.prof"
echo "Compare: go tool pprof -diff_base ${RESULTS_DIR}/heap_before.prof ${RESULTS_DIR}/heap_after.prof"

echo ""
echo "Results saved to: ${RESULTS_DIR}"
echo "============================================"

exit ${SIPP_EXIT}
