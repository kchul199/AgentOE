#!/bin/sh
# test/benchmark/profile_memory.sh
# Hardcore Developer Mode: Zero-dependency (except Docker) end-to-end memory diffing.

set -e

echo "================================================="
echo " VBGW Memory Defect & Performance Profiler "
echo "================================================="

TARGET_URL=${TARGET_URL:-"http://host.docker.internal:8080"}
ADMIN_API_KEY=${ADMIN_API_KEY:-"hardcore-admin-key"}
HOST_PP_URL=${HOST_PP_URL:-"http://127.0.0.1:8080"}

# 1. Capture baseline memory profile
echo "\n[1/4] Capturing Baseline Memory Profile via PPROF..."
curl -s -f -H "Authorization: Bearer $ADMIN_API_KEY" "$HOST_PP_URL/debug/pprof/heap" --output base_heap.pb.gz || { echo "ERROR: Could not hit $HOST_PP_URL/debug/pprof/heap. Is Orchestrator running with RUNTIME_PROFILE=dev?"; exit 1; }

# 2. Run k6 dialplan test (Inside Docker, using mapped file path technique)
echo "\n[2/4] Executing K6 Dialplan Thundering Herd Test..."
# Note: On mac/linux, we pipe the file into the k6 docker container 
docker run --rm -i -e TARGET_URL="$TARGET_URL/api/v1/fs/dialplan" grafana/k6 run - < $(dirname $0)/load_dialplan.js

# 3. Run k6 capacity bulk test
echo "\n[3/4] Executing K6 Outbound Burst Allocation Test..."
docker run --rm -i -e API_KEY="$ADMIN_API_KEY" -e TARGET_URL="$TARGET_URL/api/v1/calls" grafana/k6 run - < $(dirname $0)/load_session.js

# 4. Capture post-load memory profile
echo "\n[4/4] Capturing Post-Load Memory Profile Dataset..."
curl -s -f -H "Authorization: Bearer $ADMIN_API_KEY" "$HOST_PP_URL/debug/pprof/heap" --output post_heap.pb.gz || { echo "ERROR: Post-load pprof fetch failed!"; exit 1; }

echo "\n================================================="
echo "Profiling Complete Successfully!"
echo "Baseline and Post-Load profiles saved to: base_heap.pb.gz, post_heap.pb.gz"
echo "To analyze explicit memory leaks (-base detects what didn't get GC'd):"
echo "  go tool pprof -http=:8081 -base base_heap.pb.gz post_heap.pb.gz"
echo "================================================="
