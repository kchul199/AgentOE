#!/usr/bin/env bash
# run_10cps_10min.sh — SIPp 10 CPS × 10분 실콜 부하 테스트 오케스트레이터
# 변경이력: v1.0.0 | 2026-05-05 | Phase-T | agentoe 전체 스택 실콜 SLO 검증
#
# Usage:
#   ./services/freeswitch/scripts/run_10cps_10min.sh [OPTIONS]
#
# Options:
#   --target  IP[:PORT]  FreeSwitch SIP 주소 (기본: 127.0.0.1:5060)
#   --cps     N          초당 콜 수 (기본: 10)
#   --dur     SECONDS    총 실행 시간 (기본: 600 = 10분)
#   --docker             SIPp를 로컬 바이너리 대신 Docker 컨테이너로 실행
#   --no-wait            스택 헬스 체크 생략
#   --dry-run            SIPp 명령만 출력하고 실제 실행 안 함
#
# 실행 전제:
#   - vbgw 스택 실행 중 (docker compose -f docker/compose.vbgw.yml up -d)
#   - SIPp 설치 (apt install sipp / brew install sipp) 또는 --docker 플래그
#   - services/freeswitch/results/ 디렉토리 쓰기 권한
#
# 테스트 파라미터 (10 CPS × 10분):
#   -r 10 -rp 1000  → 1000ms당 10콜 = 10 CPS
#   -d 30000        → 통화 유지 30초
#   -l 300          → 최대 동시 300콜 (10 CPS × 30s = 300 steady-state)
#   -m 6000         → 총 6,000콜 (10 CPS × 600s)
#
# SLO 기준 (docs/reference/slo.md):
#   - 성공률  ≥ 99.9%
#   - P95 응답 시간 ≤ 500 ms (INVITE → 200 OK)
#   - 에러 버짓 소진율 < 80%

set -euo pipefail

# ─── 파라미터 파싱 ────────────────────────────────────────────────────────────
TARGET="127.0.0.1:5060"
CPS=10
DURATION_SEC=600
USE_DOCKER=false
SKIP_WAIT=false
DRY_RUN=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --cps)    CPS="$2";    shift 2 ;;
    --dur)    DURATION_SEC="$2"; shift 2 ;;
    --docker) USE_DOCKER=true; shift ;;
    --no-wait) SKIP_WAIT=true; shift ;;
    --dry-run) DRY_RUN=true; shift ;;
    *) echo "알 수 없는 옵션: $1" >&2; exit 1 ;;
  esac
done

# ─── 경로 계산 ────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SIPP_DIR="${SCRIPT_DIR}/../tests/sipp"
RESULTS_BASE="${SCRIPT_DIR}/../results"
RUN_ID="10cps_$(date +%Y%m%d_%H%M%S)"
RESULTS_DIR="${RESULTS_BASE}/${RUN_ID}"
SCENARIO="${SIPP_DIR}/uac_10cps_audio.xml"

mkdir -p "${RESULTS_DIR}"

# ─── SIPp 파라미터 계산 ───────────────────────────────────────────────────────
CALL_HOLD_MS=30000                           # 통화 유지 30초
CONCURRENT=$(( CPS * (CALL_HOLD_MS / 1000) ))  # 10 × 30 = 300
TOTAL_CALLS=$(( CPS * DURATION_SEC ))        # 10 × 600 = 6000
MEDIA_IP="${TARGET%%:*}"                     # IP 부분만 추출

# ─── 배너 ──────────────────────────────────────────────────────────────────────
echo "╔══════════════════════════════════════════════════════╗"
echo "║  agentoe SIPp 실콜 부하 테스트 — 10 CPS × 10분       ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "  타깃     : ${TARGET}"
echo "  CPS      : ${CPS} calls/sec"
echo "  통화유지  : $((CALL_HOLD_MS / 1000))s"
echo "  동시콜   : ${CONCURRENT}"
echo "  총 콜수  : ${TOTAL_CALLS} ($(( DURATION_SEC / 60 ))분)"
echo "  시나리오  : ${SCENARIO}"
echo "  결과 경로 : ${RESULTS_DIR}"
echo ""

# ─── 전제 확인 ────────────────────────────────────────────────────────────────
check_command() {
  if ! command -v "$1" &>/dev/null; then
    echo "❌ '$1' 명령을 찾을 수 없습니다. 설치 후 재시도하세요." >&2
    exit 1
  fi
}

if [[ "${USE_DOCKER}" == "false" && "${DRY_RUN}" == "false" ]]; then
  check_command sipp
fi
if [[ "${SKIP_WAIT}" == "false" ]]; then
  check_command curl
fi

if [[ ! -f "${SCENARIO}" ]]; then
  echo "❌ 시나리오 파일 없음: ${SCENARIO}" >&2
  exit 1
fi

# ─── 스택 헬스 체크 ──────────────────────────────────────────────────────────
if [[ "${SKIP_WAIT}" == "false" ]]; then
  echo "[1/5] vbgw 스택 헬스 체크..."
  FS_HOST="${TARGET%%:*}"
  FS_PORT="${TARGET##*:}"
  MAX_WAIT=60
  WAITED=0

  # FreeSwitch ESL 포트(8021) 응답 대기
  while ! nc -z "${FS_HOST}" 8021 2>/dev/null; do
    if (( WAITED >= MAX_WAIT )); then
      echo "⚠️  FreeSwitch ESL(8021) 응답 없음 — --no-wait 로 건너뛸 수 있음" >&2
      break
    fi
    echo "   FreeSwitch 대기 중... ${WAITED}s"
    sleep 5
    WAITED=$(( WAITED + 5 ))
  done
  echo "   ✓ FreeSwitch 응답 확인"

  # Orchestrator 헬스 엔드포인트
  ORCH_HOST="${FS_HOST}"
  ORCH_PORT=8080
  if curl -sf --max-time 3 "http://${ORCH_HOST}:${ORCH_PORT}/live" >/dev/null 2>&1; then
    echo "   ✓ Orchestrator 응답 확인"
  else
    echo "   ⚠️  Orchestrator 헬스 응답 없음 (테스트 계속 진행)"
  fi
else
  echo "[1/5] 헬스 체크 생략 (--no-wait)"
fi

# ─── 베이스라인 메트릭 수집 ──────────────────────────────────────────────────
echo "[2/5] 베이스라인 메트릭 수집..."
ORCH_BASE="http://${TARGET%%:*}:8080"
BACKEND_BASE="http://${TARGET%%:*}:8000"

curl -sf --max-time 5 "${ORCH_BASE}/metrics"  > "${RESULTS_DIR}/metrics_before.txt"  2>/dev/null || true
curl -sf --max-time 5 "${BACKEND_BASE}/api/v1/metrics/prometheus" \
     > "${RESULTS_DIR}/backend_metrics_before.txt" 2>/dev/null || true
curl -sf --max-time 5 "${BACKEND_BASE}/api/v1/health/live" \
     > "${RESULTS_DIR}/health_before.json"    2>/dev/null || true
echo "   ✓ 베이스라인 저장 → ${RESULTS_DIR}/metrics_before.txt"

# ─── SIPp 명령 조립 ──────────────────────────────────────────────────────────
SIPP_COMMON_ARGS=(
  -sf "${SCENARIO}"
  -s  7000
  -r  "${CPS}"
  -rp 1000
  -d  "${CALL_HOLD_MS}"
  -l  "${CONCURRENT}"
  -m  "${TOTAL_CALLS}"
  -mi "${MEDIA_IP}"
  -mp 20000
  -trace_stat
  -stf "${RESULTS_DIR}/stats.csv"
  -trace_err
  -ef  "${RESULTS_DIR}/errors.log"
  -trace_screen
  -screen_file "${RESULTS_DIR}/screen.log"
)

if [[ "${USE_DOCKER}" == "true" ]]; then
  # Docker 실행 — vbgw-net 에 연결, 시나리오 볼륨 마운트
  SIPP_CMD=(
    docker compose
      -f "$(dirname "${SCRIPT_DIR}")/../docker/compose.sipp.yml"
      run --rm
      -v "${RESULTS_DIR}:/results"
      sipp
      "${SIPP_COMMON_ARGS[@]}"
      "${TARGET}"
  )
else
  SIPP_CMD=(
    sipp
    "${SIPP_COMMON_ARGS[@]}"
    "${TARGET}"
  )
fi

# ─── Dry-run ─────────────────────────────────────────────────────────────────
if [[ "${DRY_RUN}" == "true" ]]; then
  echo ""
  echo "── Dry-run 모드: 실제 실행 없이 명령 출력 ──"
  echo "${SIPP_CMD[*]}"
  exit 0
fi

# ─── SIPp 실행 ───────────────────────────────────────────────────────────────
echo "[3/5] SIPp 실행 시작 (${CPS} CPS × ${DURATION_SEC}s)..."
echo "   진행 로그: ${RESULTS_DIR}/screen.log"
echo "   통계 CSV : ${RESULTS_DIR}/stats.csv"
echo ""

START_TS=$(date +%s)

# SIPp 실행 — 오류 발생해도 이후 단계(메트릭 수집/분석) 진행
set +e
"${SIPP_CMD[@]}" 2>&1 | tee "${RESULTS_DIR}/sipp_output.log"
SIPP_EXIT=$?
set -e

END_TS=$(date +%s)
ELAPSED=$(( END_TS - START_TS ))
echo ""
echo "   SIPp 종료 (exit=${SIPP_EXIT}, 경과=${ELAPSED}s)"

# ─── 종료 후 메트릭 수집 ─────────────────────────────────────────────────────
echo "[4/5] 종료 후 메트릭 수집..."
curl -sf --max-time 5 "${ORCH_BASE}/metrics"  > "${RESULTS_DIR}/metrics_after.txt"  2>/dev/null || true
curl -sf --max-time 5 "${BACKEND_BASE}/api/v1/metrics/prometheus" \
     > "${RESULTS_DIR}/backend_metrics_after.txt" 2>/dev/null || true
curl -sf --max-time 5 "${BACKEND_BASE}/api/v1/health/live" \
     > "${RESULTS_DIR}/health_after.json"     2>/dev/null || true
echo "   ✓ 종료 후 메트릭 저장"

# ─── SLO 분석 ────────────────────────────────────────────────────────────────
echo "[5/5] SLO 분석..."
ANALYZER="${SCRIPT_DIR}/parse_sipp_results.py"

if [[ -f "${ANALYZER}" ]] && command -v python3 &>/dev/null; then
  python3 "${ANALYZER}" \
    --stats    "${RESULTS_DIR}/stats.csv" \
    --slo-p95  500 \
    --slo-success 99.9 \
    --run-id   "${RUN_ID}" \
    2>&1 | tee "${RESULTS_DIR}/slo_report.txt"
  ANALYSIS_EXIT=$?
else
  echo "   ⚠️  parse_sipp_results.py 또는 python3 없음 — 수동으로 ${RESULTS_DIR}/stats.csv 확인"
  ANALYSIS_EXIT=0
fi

# ─── 최종 요약 ───────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════════════════════╗"
echo "║  테스트 완료                                          ║"
echo "╚══════════════════════════════════════════════════════╝"
echo "  결과 디렉토리  : ${RESULTS_DIR}"
echo "  SIPp 종료코드  : ${SIPP_EXIT}"
echo "  분석 종료코드  : ${ANALYSIS_EXIT}"
echo ""
echo "  파일 목록:"
ls -lh "${RESULTS_DIR}/" 2>/dev/null | awk '{print "    " $0}'
echo ""
echo "  SLO 리포트 재실행:"
echo "    python3 ${ANALYZER} --stats ${RESULTS_DIR}/stats.csv"
echo ""

# SIPp 자체 오류 시 비정상 종료
if (( SIPP_EXIT != 0 )); then
  echo "⚠️  SIPp 비정상 종료 — errors.log 확인: ${RESULTS_DIR}/errors.log"
  exit "${SIPP_EXIT}"
fi

exit "${ANALYSIS_EXIT}"
