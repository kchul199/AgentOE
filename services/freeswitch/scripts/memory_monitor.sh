#!/usr/bin/env bash
# memory_monitor.sh — 72h 메모리 누수 감지 (pprof heap 주기 수집)
# 변경이력: v1.0.0 | 2026-04-07 | [Implementer] | Phase 3 | 72h 메모리 모니터링
#
# Usage: ./scripts/memory_monitor.sh [TARGET_IP] [INTERVAL_HOURS] [TOTAL_HOURS]
#
# Collects heap profiles at regular intervals and generates comparison commands.

set -euo pipefail

TARGET_IP="${1:-127.0.0.1}"
INTERVAL_HOURS="${2:-4}"       # every 4 hours
TOTAL_HOURS="${3:-72}"         # 72 hours total

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RESULTS_DIR="${SCRIPT_DIR}/../results/memory_$(date +%Y%m%d_%H%M%S)"

mkdir -p "${RESULTS_DIR}"

INTERVAL_SECS=$((INTERVAL_HOURS * 3600))
ITERATIONS=$((TOTAL_HOURS / INTERVAL_HOURS))

echo "============================================"
echo " 72h Memory Leak Detection"
echo "============================================"
echo " Target:     http://${TARGET_IP}:8080"
echo " Interval:   ${INTERVAL_HOURS}h"
echo " Duration:   ${TOTAL_HOURS}h (${ITERATIONS} snapshots)"
echo " Results:    ${RESULTS_DIR}"
echo "============================================"

# Baseline
echo "[0/${ITERATIONS}] Capturing baseline (0h)..."
curl -s "http://${TARGET_IP}:8080/debug/pprof/heap" > "${RESULTS_DIR}/heap_0h.prof"
curl -s "http://${TARGET_IP}:8080/debug/pprof/goroutine" > "${RESULTS_DIR}/goroutine_0h.prof"
echo "  heap_0h.prof saved"

for i in $(seq 1 "${ITERATIONS}"); do
    HOUR=$((i * INTERVAL_HOURS))
    echo ""
    echo "Sleeping ${INTERVAL_HOURS}h until snapshot ${i}/${ITERATIONS} (${HOUR}h mark)..."
    sleep "${INTERVAL_SECS}"

    echo "[${i}/${ITERATIONS}] Capturing snapshot at ${HOUR}h..."
    curl -s "http://${TARGET_IP}:8080/debug/pprof/heap" > "${RESULTS_DIR}/heap_${HOUR}h.prof"
    curl -s "http://${TARGET_IP}:8080/debug/pprof/goroutine" > "${RESULTS_DIR}/goroutine_${HOUR}h.prof"

    # Quick inuse_space comparison
    echo "  heap_${HOUR}h.prof saved"

    # Log active calls count
    ACTIVE=$(curl -s "http://${TARGET_IP}:8080/metrics" 2>/dev/null | grep "^vbgw_active_calls " | awk '{print $2}' || echo "?")
    echo "  Active calls: ${ACTIVE}"
done

echo ""
echo "============================================"
echo " Collection Complete"
echo "============================================"
echo ""
echo "Comparison commands:"
echo ""
echo "  # Baseline vs 24h"
echo "  go tool pprof -diff_base ${RESULTS_DIR}/heap_0h.prof ${RESULTS_DIR}/heap_24h.prof"
echo ""
echo "  # Baseline vs 48h"
echo "  go tool pprof -diff_base ${RESULTS_DIR}/heap_0h.prof ${RESULTS_DIR}/heap_48h.prof"
echo ""
echo "  # Baseline vs 72h (final)"
echo "  go tool pprof -diff_base ${RESULTS_DIR}/heap_0h.prof ${RESULTS_DIR}/heap_72h.prof"
echo ""
echo "  # Goroutine count check"
echo "  go tool pprof ${RESULTS_DIR}/goroutine_72h.prof"
echo ""
echo "Pass criteria: inuse_space should NOT show linear growth"
echo "============================================"
