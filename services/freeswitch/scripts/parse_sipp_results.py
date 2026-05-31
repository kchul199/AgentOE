#!/usr/bin/env python3
"""parse_sipp_results.py — SIPp CSV 통계 파싱 & SLO 분석

변경이력: v1.0.0 | 2026-05-05 | Phase-T | agentoe 실콜 SLO 검증

SIPp -trace_stat 로 생성된 stats.csv 를 읽어:
  1. ResponseTimeRepartition 버킷에서 P50 / P95 / P99 추정
  2. 성공률(2xx / 총 콜) 계산
  3. SLO 기준(docs/reference/slo.md) 대비 PASS / FAIL 판정
  4. 에러 버짓 소진율 계산

사용법:
    python3 parse_sipp_results.py \\
        --stats   results/10cps_20260505_120000/stats.csv \\
        --slo-p95 500 \\
        --slo-success 99.9 \\
        [--run-id <str>] [--json] [--fail-on-slo-breach]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── SIPp ResponseTimeRepartition 버킷 상한 (ms) ──────────────────────────────
# uac_10cps_audio.xml 에서 value="50,100,150,200,300,500,750,1000,2000,5000"
RTD_BUCKETS_MS: list[int] = [50, 100, 150, 200, 300, 500, 750, 1000, 2000, 5000]

# SLO 기본값 (docs/reference/slo.md 기준)
DEFAULT_SLO_P95_MS: float = 500.0
DEFAULT_SLO_SUCCESS_PCT: float = 99.9
SLO_TARGET_SUCCESS: float = 0.999       # 99.9%
ERROR_BUDGET_WARN_PCT: float = 80.0     # 80% 소진 시 경고
BURN_RATE_PAGE_THRESHOLD: float = 14.4  # 즉시 페이지 임계값


@dataclass
class SippStats:
    """SIPp stats.csv 의 마지막(=누적) 행 파싱 결과."""

    run_id: str = ""

    # 콜 카운트
    total_calls: int = 0
    successful_calls: int = 0     # 2xx (CallsSuccessful)
    failed_calls: int = 0         # FailedCall
    retransmissions: int = 0

    # RTD 히스토그램 (버킷별 누적 카운트)
    # rtd_counts[i] = ≤ RTD_BUCKETS_MS[i] 인 콜 수
    rtd_counts: list[int] = field(default_factory=list)

    # SIPp 자체 통계
    call_rate: float = 0.0        # calls/s (평균)
    duration_ms: int = 0          # 총 실행 시간

    # ── 파생 지표 ─────────────────────────────────────────────────────────
    @property
    def success_pct(self) -> float:
        if self.total_calls == 0:
            return 0.0
        return self.successful_calls / self.total_calls * 100

    @property
    def error_rate(self) -> float:
        """0.0 ~ 1.0"""
        if self.total_calls == 0:
            return 0.0
        return self.failed_calls / self.total_calls

    @property
    def burn_rate(self) -> float:
        """error_rate / (1 - SLO_target)"""
        if self.error_rate == 0:
            return 0.0
        allowed_error_rate = 1.0 - SLO_TARGET_SUCCESS
        return self.error_rate / allowed_error_rate

    @property
    def error_budget_consumed_pct(self) -> float:
        """에러 버짓 소진율(%)"""
        return min(self.burn_rate * 100 / 14.4 * ERROR_BUDGET_WARN_PCT, 100.0)

    def percentile_ms(self, pct: float) -> Optional[float]:
        """RTD 히스토그램에서 pct 퍼센타일 추정.

        선형 보간(버킷 내 균등 분포 가정).
        """
        if not self.rtd_counts or self.successful_calls == 0:
            return None

        target = pct / 100.0 * self.successful_calls
        cumulative = 0
        prev_upper = 0

        for i, count in enumerate(self.rtd_counts):
            cumulative += count
            upper = RTD_BUCKETS_MS[i] if i < len(RTD_BUCKETS_MS) else RTD_BUCKETS_MS[-1]
            if cumulative >= target:
                # 버킷 내 선형 보간
                bucket_count = count
                if bucket_count == 0:
                    return float(upper)
                position_in_bucket = (target - (cumulative - bucket_count)) / bucket_count
                estimated = prev_upper + position_in_bucket * (upper - prev_upper)
                return round(estimated, 1)
            prev_upper = upper

        # 마지막 버킷 초과 — 5000ms 이상
        return float(RTD_BUCKETS_MS[-1])


# ── CSV 파싱 ──────────────────────────────────────────────────────────────────

def _find_column(header: list[str], *candidates: str) -> Optional[int]:
    """대소문자 무시 후보 목록 중 첫 번째 매칭 컬럼 인덱스 반환."""
    lower_header = [h.strip().lower() for h in header]
    for name in candidates:
        try:
            return lower_header.index(name.lower())
        except ValueError:
            continue
    return None


def _parse_rtd_columns(header: list[str], row: list[str]) -> list[int]:
    """ResponseTimeRepartition 컬럼들 파싱.

    SIPp CSV 헤더 예:
      ResponseTime1(50ms);ResponseTime2(100ms);...;ResponseTime10(5000ms)
    또는
      RTD1_NB_Pkt_Sent;RTD2_NB_Pkt_Sent;...
    """
    counts: list[int] = []

    # 방법 1: ResponseTime{n}({bucket}ms) 패턴
    for bucket_ms in RTD_BUCKETS_MS:
        col_idx = None
        for pattern in [
            f"responsetime",
            f"rtd",
        ]:
            for i, h in enumerate(header):
                h_lower = h.strip().lower()
                if pattern in h_lower and f"{bucket_ms}ms" in h_lower:
                    col_idx = i
                    break
            if col_idx is not None:
                break

        if col_idx is not None and col_idx < len(row):
            try:
                counts.append(int(row[col_idx].strip() or "0"))
            except ValueError:
                counts.append(0)
        else:
            counts.append(0)

    # 방법 2: 컬럼을 못 찾으면 순서 기반 fallback
    #   SIPp 3.6+ CSV: 컬럼 순서는 버전에 따라 다름 — 0으로 채움
    if all(c == 0 for c in counts):
        # 헤더에서 'responsetime' 또는 'rtd_nb' 포함 컬럼 순서대로 수집
        rtd_indices = [
            i for i, h in enumerate(header)
            if "responsetime" in h.strip().lower() or "rtd_nb_pkt" in h.strip().lower()
        ]
        counts = []
        for i, idx in enumerate(rtd_indices[: len(RTD_BUCKETS_MS)]):
            if idx < len(row):
                try:
                    counts.append(int(row[idx].strip() or "0"))
                except ValueError:
                    counts.append(0)
            else:
                counts.append(0)
        # 부족하면 0 패딩
        while len(counts) < len(RTD_BUCKETS_MS):
            counts.append(0)

    return counts


def parse_stats_csv(csv_path: Path, run_id: str = "") -> SippStats:
    """SIPp stats.csv 파싱 → SippStats.

    SIPp 는 주기적으로 행을 append 하며 마지막 행이 최종 누적값.
    구분자는 ';' (SIPp 기본값).
    """
    stats = SippStats(run_id=run_id)

    if not csv_path.exists():
        raise FileNotFoundError(f"stats.csv 파일 없음: {csv_path}")

    last_row: Optional[list[str]] = None
    header: list[str] = []

    with csv_path.open(newline="", encoding="utf-8-sig") as f:
        # SIPp CSV 구분자는 세미콜론
        reader = csv.reader(f, delimiter=";")
        for i, row in enumerate(reader):
            if i == 0:
                header = row
                continue
            if any(cell.strip() for cell in row):
                last_row = row

    if last_row is None:
        raise ValueError(f"stats.csv 가 비어 있습니다: {csv_path}")

    def _int(col_idx: Optional[int]) -> int:
        if col_idx is None or col_idx >= len(last_row):
            return 0
        try:
            return int(last_row[col_idx].strip() or "0")
        except ValueError:
            return 0

    def _float(col_idx: Optional[int]) -> float:
        if col_idx is None or col_idx >= len(last_row):
            return 0.0
        try:
            return float(last_row[col_idx].strip() or "0")
        except ValueError:
            return 0.0

    # ── 주요 컬럼 추출 ─────────────────────────────────────────────────────
    stats.total_calls = _int(_find_column(header,
        "TotalCallCreated", "Total Call Created", "CallsCreated"))
    stats.successful_calls = _int(_find_column(header,
        "SuccessfulCall", "Successful call", "CallsSuccessful", "SuccessfulCalls"))
    stats.failed_calls = _int(_find_column(header,
        "FailedCall", "Failed call", "CallsFailed", "FailedCalls"))
    stats.retransmissions = _int(_find_column(header,
        "Retransmissions", "Retrans", "NbOfRetrans"))
    stats.call_rate = _float(_find_column(header,
        "CallRate", "Call rate", "CurrentCallRate"))
    stats.duration_ms = _int(_find_column(header,
        "ElapsedTime(P)", "ElapsedTime", "TestDuration"))

    # 총 콜수 fallback: successful + failed
    if stats.total_calls == 0:
        stats.total_calls = stats.successful_calls + stats.failed_calls

    # RTD 히스토그램
    stats.rtd_counts = _parse_rtd_columns(header, last_row)

    return stats


# ── SLO 판정 ─────────────────────────────────────────────────────────────────

@dataclass
class SloResult:
    passed: bool
    checks: dict[str, bool]
    metrics: dict[str, float]
    warnings: list[str] = field(default_factory=list)


def evaluate_slo(
    stats: SippStats,
    slo_p95_ms: float,
    slo_success_pct: float,
) -> SloResult:
    p50 = stats.percentile_ms(50) or 0.0
    p95 = stats.percentile_ms(95) or 0.0
    p99 = stats.percentile_ms(99) or 0.0

    checks = {
        "success_rate": stats.success_pct >= slo_success_pct,
        "p95_latency":  p95 <= slo_p95_ms,
        "burn_rate":    stats.burn_rate < BURN_RATE_PAGE_THRESHOLD,
        "error_budget": stats.error_budget_consumed_pct < ERROR_BUDGET_WARN_PCT,
    }

    warnings: list[str] = []
    if stats.retransmissions > 0:
        retx_pct = stats.retransmissions / max(stats.total_calls, 1) * 100
        if retx_pct > 1.0:
            warnings.append(f"재전송률 {retx_pct:.1f}% > 1% — 네트워크 품질 점검 필요")
    if stats.burn_rate >= BURN_RATE_PAGE_THRESHOLD:
        warnings.append(
            f"🚨 burn rate {stats.burn_rate:.1f} ≥ {BURN_RATE_PAGE_THRESHOLD} — 즉시 온콜 알람 기준"
        )

    return SloResult(
        passed=all(checks.values()),
        checks=checks,
        metrics={
            "total_calls":           float(stats.total_calls),
            "successful_calls":      float(stats.successful_calls),
            "failed_calls":          float(stats.failed_calls),
            "success_pct":           round(stats.success_pct, 3),
            "error_rate":            round(stats.error_rate * 100, 3),
            "p50_ms":                p50,
            "p95_ms":                p95,
            "p99_ms":                p99,
            "burn_rate":             round(stats.burn_rate, 2),
            "error_budget_consumed": round(stats.error_budget_consumed_pct, 1),
            "avg_call_rate":         round(stats.call_rate, 2),
            "retransmissions":       float(stats.retransmissions),
        },
        warnings=warnings,
    )


# ── 리포트 출력 ───────────────────────────────────────────────────────────────

def print_report(
    stats: SippStats,
    result: SloResult,
    slo_p95_ms: float,
    slo_success_pct: float,
) -> None:
    CHECK = "✅"
    FAIL  = "❌"
    WARN  = "⚠️ "

    def mark(passed: bool) -> str:
        return CHECK if passed else FAIL

    m = result.metrics

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  SIPp 실콜 SLO 분석 리포트  run_id={stats.run_id or '(없음)'}")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("[ 콜 통계 ]")
    print(f"  총 콜        : {int(m['total_calls']):,}")
    print(f"  성공 (2xx)   : {int(m['successful_calls']):,}")
    print(f"  실패         : {int(m['failed_calls']):,}")
    print(f"  재전송       : {int(m['retransmissions']):,}")
    print(f"  평균 CPS     : {m['avg_call_rate']:.2f} calls/s")
    print()
    print("[ 응답 시간 (INVITE → 200 OK) ]")
    print(f"  P50          : {m['p50_ms']:.0f} ms")
    print(f"  P95          : {m['p95_ms']:.0f} ms  (SLO ≤ {slo_p95_ms:.0f} ms)")
    print(f"  P99          : {m['p99_ms']:.0f} ms")
    print()
    print("[ SLO 판정 ]")
    print(f"  {mark(result.checks['success_rate'])} 성공률     {m['success_pct']:.3f}%"
          f"  (SLO ≥ {slo_success_pct:.1f}%)")
    print(f"  {mark(result.checks['p95_latency'])} P95 지연    {m['p95_ms']:.0f} ms"
          f"  (SLO ≤ {slo_p95_ms:.0f} ms)")
    print(f"  {mark(result.checks['burn_rate'])} Burn rate   {m['burn_rate']:.2f}"
          f"  (페이지 임계 < {BURN_RATE_PAGE_THRESHOLD})")
    print(f"  {mark(result.checks['error_budget'])} 에러 버짓  {m['error_budget_consumed']:.1f}%"
          f"  소진 (경고 < {ERROR_BUDGET_WARN_PCT:.0f}%)")
    print()

    if result.warnings:
        print("[ 경고 ]")
        for w in result.warnings:
            print(f"  {WARN} {w}")
        print()

    overall = f"{CHECK} PASS" if result.passed else f"{FAIL} FAIL"
    print(f"  최종 결과    : {overall}")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ── CLI ──────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="SIPp stats.csv → SLO 분석 리포트"
    )
    parser.add_argument("--stats",        required=True, help="SIPp stats.csv 경로")
    parser.add_argument("--slo-p95",      type=float, default=DEFAULT_SLO_P95_MS,
                        help=f"P95 SLO (ms, 기본 {DEFAULT_SLO_P95_MS:.0f})")
    parser.add_argument("--slo-success",  type=float, default=DEFAULT_SLO_SUCCESS_PCT,
                        help=f"성공률 SLO (%%, 기본 {DEFAULT_SLO_SUCCESS_PCT:.1f})")
    parser.add_argument("--run-id",       default="", help="실행 식별자 (리포트 헤더용)")
    parser.add_argument("--json",         action="store_true", help="JSON 형식 출력")
    parser.add_argument("--fail-on-slo-breach", action="store_true",
                        help="SLO 위반 시 exit code 1 반환")
    args = parser.parse_args()

    try:
        stats = parse_stats_csv(Path(args.stats), run_id=args.run_id)
    except (FileNotFoundError, ValueError) as e:
        print(f"❌ CSV 파싱 오류: {e}", file=sys.stderr)
        return 2

    result = evaluate_slo(
        stats,
        slo_p95_ms=args.slo_p95,
        slo_success_pct=args.slo_success,
    )

    if args.json:
        output = {
            "run_id":  stats.run_id,
            "passed":  result.passed,
            "checks":  result.checks,
            "metrics": result.metrics,
            "warnings": result.warnings,
            "slo_thresholds": {
                "success_pct": args.slo_success,
                "p95_ms":      args.slo_p95,
                "burn_rate_page": BURN_RATE_PAGE_THRESHOLD,
                "error_budget_warn_pct": ERROR_BUDGET_WARN_PCT,
            },
        }
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        print_report(stats, result, slo_p95_ms=args.slo_p95,
                     slo_success_pct=args.slo_success)

    if args.fail_on_slo_breach and not result.passed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
