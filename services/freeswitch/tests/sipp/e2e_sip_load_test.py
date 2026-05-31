#!/usr/bin/env python3
"""e2e_sip_load_test.py — Python 기반 SIP E2E 부하 테스트

sipp 바이너리 없이 Python 소켓으로 SIP UAC/UAS 구현.
FreeSwitch 스택이 없는 환경에서도 전체 SIP 콜 흐름과 SLO 분석 파이프라인 검증.

실콜 흐름:
  UAC ──INVITE──► UAS (:15060)
      ◄──100────── UAS (10ms 후)
      ◄──180────── UAS (20ms 후)
      ◄──200 OK─── UAS (latency 시뮬레이션 후)
      ──ACK──────► UAS
      ~~ 통화 유지 (hold_ms) ~~
      ──BYE──────► UAS
      ◄──200 OK─── UAS

사용법:
    python3 e2e_sip_load_test.py [--cps 10] [--dur 60] [--hold 1] [--workers 4]
    python3 e2e_sip_load_test.py --cps 10 --dur 60  # 기본 60초 10 CPS
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import dataclasses
import io
import logging
import os
import random
import socket
import sys
import time
import threading
from collections import defaultdict
from pathlib import Path
from typing import Optional

logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("sip_e2e")

# ── 상수 ──────────────────────────────────────────────────────────────────────
UAS_HOST = "127.0.0.1"
UAS_PORT = 15060          # FreeSwitch 대역 외 포트 사용
UAC_BASE_PORT = 15100     # UAC 소켓 시작 포트
BUFSIZE = 4096
SIP_VERSION = "SIP/2.0"

# RTD 버킷 (ms) — parse_sipp_results.py 와 동일
RTD_BUCKETS_MS = [50, 100, 150, 200, 300, 500, 750, 1000, 2000, 5000]


# ── 데이터 클래스 ─────────────────────────────────────────────────────────────
@dataclasses.dataclass
class CallRecord:
    call_id: str
    t_invite: float = 0.0      # INVITE 전송 시각
    t_200ok: float = 0.0       # 200 OK 수신 시각
    t_bye: float = 0.0         # BYE 전송 시각
    t_end: float = 0.0         # 세션 종료 시각
    success: bool = False
    error: str = ""

    @property
    def setup_ms(self) -> float:
        """INVITE → 200 OK 응답 시간 (ms)"""
        if self.t_200ok > 0 and self.t_invite > 0:
            return (self.t_200ok - self.t_invite) * 1000
        return 0.0


@dataclasses.dataclass
class LoadStats:
    total: int = 0
    successful: int = 0
    failed: int = 0
    rtd_counts: list = dataclasses.field(
        default_factory=lambda: [0] * len(RTD_BUCKETS_MS)
    )
    setup_times_ms: list = dataclasses.field(default_factory=list)
    start_ts: float = 0.0
    end_ts: float = 0.0

    def add(self, rec: CallRecord) -> None:
        self.total += 1
        if rec.success:
            self.successful += 1
            ms = rec.setup_ms
            self.setup_times_ms.append(ms)
            for i, bucket in enumerate(RTD_BUCKETS_MS):
                if ms <= bucket:
                    self.rtd_counts[i] += 1
                    break
        else:
            self.failed += 1

    @property
    def success_pct(self) -> float:
        return self.successful / max(self.total, 1) * 100

    @property
    def elapsed_sec(self) -> float:
        return max(self.end_ts - self.start_ts, 0.001)

    @property
    def avg_cps(self) -> float:
        return self.total / self.elapsed_sec

    def percentile(self, pct: float) -> float:
        if not self.setup_times_ms:
            return 0.0
        s = sorted(self.setup_times_ms)
        idx = int(len(s) * pct / 100)
        return s[min(idx, len(s) - 1)]


# ── SIP 메시지 빌더 ───────────────────────────────────────────────────────────
def build_invite(
    call_id: str,
    call_num: int,
    local_ip: str,
    local_port: int,
    remote_ip: str,
    remote_port: int,
    branch: str,
) -> str:
    sdp = (
        f"v=0\r\n"
        f"o=pyuac {call_num} 1 IN IP4 {local_ip}\r\n"
        f"s=agentoe E2E Load Test\r\n"
        f"c=IN IP4 {local_ip}\r\n"
        f"t=0 0\r\n"
        f"m=audio 20000 RTP/AVP 0\r\n"
        f"a=rtpmap:0 PCMU/8000\r\n"
        f"a=sendrecv\r\n"
    )
    msg = (
        f"INVITE sip:7000@{remote_ip}:{remote_port} {SIP_VERSION}\r\n"
        f"Via: {SIP_VERSION}/UDP {local_ip}:{local_port};branch={branch}\r\n"
        f"From: \"LoadTest {call_num}\" <sip:lt{call_num}@{local_ip}>;tag={call_id[:8]}\r\n"
        f"To: <sip:7000@{remote_ip}:{remote_port}>\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 1 INVITE\r\n"
        f"Contact: <sip:lt{call_num}@{local_ip}:{local_port}>\r\n"
        f"Max-Forwards: 70\r\n"
        f"X-Tenant-ID: load-test-tenant\r\n"
        f"X-Client-ID: pyuac-{call_num}\r\n"
        f"X-Load-Test: true\r\n"
        f"Content-Type: application/sdp\r\n"
        f"Content-Length: {len(sdp.encode())}\r\n"
        f"\r\n"
        f"{sdp}"
    )
    return msg


def build_ack(
    call_id: str,
    call_num: int,
    local_ip: str,
    local_port: int,
    remote_ip: str,
    remote_port: int,
    branch: str,
    to_tag: str,
) -> str:
    return (
        f"ACK sip:7000@{remote_ip}:{remote_port} {SIP_VERSION}\r\n"
        f"Via: {SIP_VERSION}/UDP {local_ip}:{local_port};branch={branch}\r\n"
        f"From: \"LoadTest {call_num}\" <sip:lt{call_num}@{local_ip}>;tag={call_id[:8]}\r\n"
        f"To: <sip:7000@{remote_ip}:{remote_port}>;tag={to_tag}\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 1 ACK\r\n"
        f"Max-Forwards: 70\r\n"
        f"Content-Length: 0\r\n"
        f"\r\n"
    )


def build_bye(
    call_id: str,
    call_num: int,
    local_ip: str,
    local_port: int,
    remote_ip: str,
    remote_port: int,
    branch: str,
    to_tag: str,
) -> str:
    return (
        f"BYE sip:7000@{remote_ip}:{remote_port} {SIP_VERSION}\r\n"
        f"Via: {SIP_VERSION}/UDP {local_ip}:{local_port};branch={branch}\r\n"
        f"From: \"LoadTest {call_num}\" <sip:lt{call_num}@{local_ip}>;tag={call_id[:8]}\r\n"
        f"To: <sip:7000@{remote_ip}:{remote_port}>;tag={to_tag}\r\n"
        f"Call-ID: {call_id}\r\n"
        f"CSeq: 2 BYE\r\n"
        f"Max-Forwards: 70\r\n"
        f"Content-Length: 0\r\n"
        f"\r\n"
    )


def build_response(code: int, phrase: str, request_lines: list[str], to_tag: str = "") -> str:
    """UAS: request 의 Via/From/To/Call-ID/CSeq 를 그대로 에코하여 응답 생성."""
    via = ""
    frm = ""
    to = ""
    call_id = ""
    cseq = ""
    for line in request_lines:
        l = line.strip()
        if l.lower().startswith("via:"):
            via = l
        elif l.lower().startswith("from:"):
            frm = l
        elif l.lower().startswith("to:"):
            to = l
            if to_tag and "tag=" not in l.lower():
                to = l + f";tag={to_tag}"
        elif l.lower().startswith("call-id:"):
            call_id = l
        elif l.lower().startswith("cseq:"):
            cseq = l

    return (
        f"{SIP_VERSION} {code} {phrase}\r\n"
        f"{via}\r\n"
        f"{frm}\r\n"
        f"{to}\r\n"
        f"{call_id}\r\n"
        f"{cseq}\r\n"
        f"Content-Length: 0\r\n"
        f"\r\n"
    )


# ── SIP UAS (서버) ─────────────────────────────────────────────────────────────
class SipUas(threading.Thread):
    """멀티 스레드 SIP UAS — INVITE/ACK/BYE 처리, 응답 지연 시뮬레이션."""

    def __init__(
        self,
        host: str,
        port: int,
        mean_setup_ms: float = 80.0,
        jitter_ms: float = 30.0,
        error_rate: float = 0.001,   # 0.1% 실패
    ):
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.mean_setup_ms = mean_setup_ms
        self.jitter_ms = jitter_ms
        self.error_rate = error_rate
        self._sock: Optional[socket.socket] = None
        self._stop = threading.Event()
        self._active_calls: dict[str, str] = {}  # call_id → to_tag

    def run(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(0.5)
        self._sock.bind((self.host, self.port))
        log.debug(f"UAS listening on {self.host}:{self.port}")

        while not self._stop.is_set():
            try:
                data, addr = self._sock.recvfrom(BUFSIZE)
            except socket.timeout:
                continue
            except Exception:
                break
            threading.Thread(
                target=self._handle, args=(data, addr), daemon=True
            ).start()

        self._sock.close()

    def _handle(self, data: bytes, addr: tuple) -> None:
        msg = data.decode(errors="replace")
        lines = msg.splitlines()
        if not lines:
            return
        req_line = lines[0].strip()

        call_id = ""
        for line in lines:
            if line.lower().startswith("call-id:"):
                call_id = line.split(":", 1)[1].strip()
                break

        if req_line.startswith("INVITE"):
            self._handle_invite(lines, addr, call_id)
        elif req_line.startswith("ACK"):
            pass  # stateless ACK — 무시
        elif req_line.startswith("BYE"):
            self._handle_bye(lines, addr, call_id)

    def _handle_invite(self, lines: list[str], addr: tuple, call_id: str) -> None:
        assert self._sock is not None
        # 100 Trying — 즉시
        self._sock.sendto(
            build_response(100, "Trying", lines).encode(), addr
        )
        # 시뮬레이션 지연 (네트워크 + 처리 시간)
        delay_ms = max(
            10.0,
            random.gauss(self.mean_setup_ms, self.jitter_ms),
        )
        # 180 Ringing — 20ms 후
        time.sleep(0.020)
        self._sock.sendto(
            build_response(180, "Ringing", lines).encode(), addr
        )
        # 200 OK or 503 (에러율 시뮬레이션)
        remaining = max(0.0, (delay_ms - 20.0) / 1000.0)
        time.sleep(remaining)

        if random.random() < self.error_rate:
            self._sock.sendto(
                build_response(503, "Service Unavailable", lines).encode(), addr
            )
        else:
            to_tag = f"uas{call_id[:6]}"
            self._active_calls[call_id] = to_tag
            self._sock.sendto(
                build_response(200, "OK", lines, to_tag=to_tag).encode(), addr
            )

    def _handle_bye(self, lines: list[str], addr: tuple, call_id: str) -> None:
        assert self._sock is not None
        to_tag = self._active_calls.pop(call_id, "unknown")
        self._sock.sendto(
            build_response(200, "OK", lines, to_tag=to_tag).encode(), addr
        )

    def stop(self) -> None:
        self._stop.set()


# ── SIP UAC (클라이언트) ──────────────────────────────────────────────────────
def run_one_call(
    call_num: int,
    remote_ip: str,
    remote_port: int,
    hold_sec: float,
    timeout_sec: float = 5.0,
) -> CallRecord:
    """단일 SIP 콜 (INVITE → ACK → BYE) 동기 실행."""
    import uuid

    call_id = str(uuid.uuid4())
    branch = f"z9hG4bK{call_id[:12]}"
    local_ip = "127.0.0.1"
    local_port = UAC_BASE_PORT + (call_num % 500)

    rec = CallRecord(call_id=call_id)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.settimeout(timeout_sec)
    try:
        sock.bind((local_ip, local_port))
    except OSError:
        # 포트 충돌 시 임시 포트
        sock.bind((local_ip, 0))
        local_port = sock.getsockname()[1]

    invite = build_invite(
        call_id, call_num, local_ip, local_port,
        remote_ip, remote_port, branch,
    )

    rec.t_invite = time.monotonic()
    try:
        sock.sendto(invite.encode(), (remote_ip, remote_port))

        to_tag = ""
        got_200 = False
        # 프로비저널 + 200 수신 루프
        deadline = time.monotonic() + timeout_sec
        while time.monotonic() < deadline:
            try:
                data, _ = sock.recvfrom(BUFSIZE)
            except socket.timeout:
                break
            resp = data.decode(errors="replace")
            first_line = resp.split("\r\n", 1)[0]
            if "200" in first_line:
                rec.t_200ok = time.monotonic()
                # To 헤더에서 tag 추출
                for line in resp.splitlines():
                    if line.lower().startswith("to:") and "tag=" in line.lower():
                        parts = line.split("tag=", 1)
                        if len(parts) > 1:
                            to_tag = parts[1].split(";")[0].strip()
                        break
                got_200 = True
                break
            elif any(f" {c} " in first_line for c in
                     ["400", "401", "403", "404", "480", "486", "503"]):
                rec.error = first_line.strip()
                break

        if not got_200:
            if not rec.error:
                rec.error = "timeout waiting for 200 OK"
            return rec

        # ACK
        ack = build_ack(
            call_id, call_num, local_ip, local_port,
            remote_ip, remote_port,
            f"z9hG4bKack{call_id[:10]}", to_tag,
        )
        sock.sendto(ack.encode(), (remote_ip, remote_port))

        # 통화 유지
        if hold_sec > 0:
            time.sleep(hold_sec)

        # BYE
        rec.t_bye = time.monotonic()
        bye = build_bye(
            call_id, call_num, local_ip, local_port,
            remote_ip, remote_port,
            f"z9hG4bKbye{call_id[:10]}", to_tag,
        )
        sock.sendto(bye.encode(), (remote_ip, remote_port))

        # BYE 200 OK
        try:
            sock.settimeout(2.0)
            data, _ = sock.recvfrom(BUFSIZE)
        except socket.timeout:
            pass  # BYE 응답 없어도 성공으로 간주

        rec.t_end = time.monotonic()
        rec.success = True

    except Exception as e:
        rec.error = str(e)
    finally:
        sock.close()

    return rec


# ── 부하 생성기 ───────────────────────────────────────────────────────────────
def run_load_test(
    remote_ip: str,
    remote_port: int,
    cps: int,
    duration_sec: int,
    hold_sec: float,
    workers: int,
) -> LoadStats:
    """비동기 레이트 리미터 + 스레드풀로 CPS 부하 생성."""
    import concurrent.futures

    interval = 1.0 / cps
    total_calls = cps * duration_sec

    stats = LoadStats()
    stats.start_ts = time.monotonic()

    lock = threading.Lock()

    def _call(n: int) -> CallRecord:
        return run_one_call(
            call_num=n,
            remote_ip=remote_ip,
            remote_port=remote_port,
            hold_sec=hold_sec,
        )

    futures = []
    origin = time.monotonic()

    print(f"\n  총 {total_calls}콜 예약 중 ({cps} CPS × {duration_sec}s)...", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        for n in range(total_calls):
            t_fire = origin + n * interval
            sleep_s = t_fire - time.monotonic()
            if sleep_s > 0:
                time.sleep(sleep_s)
            futures.append(pool.submit(_call, n))

            # 진행 표시 (10초마다)
            if (n + 1) % (cps * 10) == 0:
                elapsed = time.monotonic() - origin
                pct = (n + 1) / total_calls * 100
                print(
                    f"  진행: {n+1}/{total_calls} ({pct:.0f}%) "
                    f"경과={elapsed:.0f}s",
                    flush=True,
                )

        print("  마지막 콜 완료 대기...", flush=True)
        for f in concurrent.futures.as_completed(futures):
            try:
                rec = f.result()
            except Exception as e:
                rec = CallRecord(call_id="err", error=str(e))
            with lock:
                stats.add(rec)

    stats.end_ts = time.monotonic()
    return stats


# ── stats.csv 생성 (parse_sipp_results.py 호환) ───────────────────────────────
def write_stats_csv(stats: LoadStats, path: Path) -> None:
    buckets_header = [f"ResponseTime{i+1}({b}ms)" for i, b in enumerate(RTD_BUCKETS_MS)]
    header = [
        "TotalCallCreated", "SuccessfulCall", "FailedCall", "Retransmissions",
        "CallRate", "ElapsedTime(P)",
    ] + buckets_header

    row = [
        str(stats.total),
        str(stats.successful),
        str(stats.failed),
        "0",
        f"{stats.avg_cps:.2f}",
        str(int(stats.elapsed_sec * 1000)),
    ] + [str(c) for c in stats.rtd_counts]

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        writer = csv.writer(f, delimiter=";")
        writer.writerow(header)
        writer.writerow(row)


# ── 리포트 출력 ───────────────────────────────────────────────────────────────
def print_report(stats: LoadStats, cps: int, duration_sec: int) -> None:
    p50  = stats.percentile(50)
    p95  = stats.percentile(95)
    p99  = stats.percentile(99)
    p_max = max(stats.setup_times_ms) if stats.setup_times_ms else 0

    slo_success = stats.success_pct >= 99.9
    slo_p95     = p95 <= 500.0
    error_rate  = stats.failed / max(stats.total, 1)
    burn_rate   = error_rate / 0.001 if error_rate > 0 else 0.0
    budget_pct  = min(burn_rate * 100, 100.0)

    CHECK = "✅"
    FAIL  = "❌"

    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print(f"  E2E SIP 부하 테스트 결과  ({cps} CPS × {duration_sec}s)")
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")
    print()
    print("[ 콜 통계 ]")
    print(f"  총 콜 수       : {stats.total:,}")
    print(f"  성공 (2xx)     : {stats.successful:,}")
    print(f"  실패           : {stats.failed:,}")
    print(f"  실제 CPS       : {stats.avg_cps:.2f} calls/s")
    print(f"  총 경과        : {stats.elapsed_sec:.1f}s")
    print()
    print("[ 응답 시간 분포 (INVITE → 200 OK) ]")
    print(f"  P50            : {p50:.1f} ms")
    print(f"  P95            : {p95:.1f} ms")
    print(f"  P99            : {p99:.1f} ms")
    print(f"  MAX            : {p_max:.1f} ms")
    print()
    print("[ RTD 히스토그램 ]")
    cumulative = 0
    for i, (bucket, count) in enumerate(zip(RTD_BUCKETS_MS, stats.rtd_counts)):
        cumulative += count
        bar = "█" * min(int(count / max(stats.successful, 1) * 40), 40)
        prev = RTD_BUCKETS_MS[i - 1] if i > 0 else 0
        print(f"  ≤{bucket:5d}ms : {count:5d}  {bar}")
    remainder = stats.successful - sum(stats.rtd_counts)
    if remainder > 0:
        print(f"  > 5000ms : {remainder:5d}")
    print()
    print("[ SLO 판정 (docs/reference/slo.md) ]")
    print(f"  {CHECK if slo_success else FAIL} 성공률    {stats.success_pct:.3f}%  (SLO ≥ 99.9%)")
    print(f"  {CHECK if slo_p95 else FAIL} P95        {p95:.1f} ms  (SLO ≤ 500 ms)")
    print(f"  {CHECK if burn_rate < 14.4 else FAIL} Burn rate  {burn_rate:.2f}  (페이지 임계 < 14.4)")
    print(f"  {CHECK if budget_pct < 80 else FAIL} 에러 버짓  {budget_pct:.1f}%  소진 (경고 < 80%)")
    overall_pass = slo_success and slo_p95 and burn_rate < 14.4
    print()
    result = f"{CHECK} PASS" if overall_pass else f"{FAIL} FAIL"
    print(f"  최종 결과  :  {result}")
    print()
    print("━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━")


# ── 메인 ─────────────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(description="Python SIP E2E 부하 테스트")
    parser.add_argument("--cps",       type=int,   default=10,    help="초당 콜 수 (기본 10)")
    parser.add_argument("--dur",       type=int,   default=60,    help="테스트 시간(초) (기본 60)")
    parser.add_argument("--hold",      type=float, default=0.5,   help="통화 유지 시간(초) (기본 0.5)")
    parser.add_argument("--workers",   type=int,   default=64,    help="스레드 풀 크기 (기본 64)")
    parser.add_argument("--uas-port",  type=int,   default=UAS_PORT, help=f"UAS 포트 (기본 {UAS_PORT})")
    parser.add_argument("--mean-ms",   type=float, default=80.0,  help="UAS 평균 응답 지연 ms (기본 80)")
    parser.add_argument("--jitter-ms", type=float, default=30.0,  help="UAS 지터 ms (기본 30)")
    parser.add_argument("--error-rate",type=float, default=0.001, help="UAS 실패율 (기본 0.001 = 0.1%%)")
    parser.add_argument("--out",       type=str,   default=None,  help="stats.csv 저장 경로")
    parser.add_argument("--target",    type=str,   default=None,
                        help="실제 FreeSwitch IP:PORT (없으면 내장 UAS 사용)")
    args = parser.parse_args()

    # 실제 타겟 vs 내장 UAS
    if args.target:
        parts = args.target.rsplit(":", 1)
        remote_ip   = parts[0]
        remote_port = int(parts[1]) if len(parts) > 1 else 5060
        uas = None
        print(f"\n  실제 FreeSwitch 타겟: {remote_ip}:{remote_port}")
    else:
        remote_ip   = UAS_HOST
        remote_port = args.uas_port
        uas = SipUas(
            host=UAS_HOST,
            port=args.uas_port,
            mean_setup_ms=args.mean_ms,
            jitter_ms=args.jitter_ms,
            error_rate=args.error_rate,
        )
        uas.start()
        time.sleep(0.1)  # UAS 바인딩 대기
        print(f"\n  내장 UAS 시작됨: {UAS_HOST}:{args.uas_port}")

    print(f"  파라미터: {args.cps} CPS × {args.dur}s, 통화유지={args.hold}s, 워커={args.workers}")
    print(f"  예상 총 콜: {args.cps * args.dur:,}개")
    if not args.target:
        print(f"  UAS 응답 시뮬레이션: μ={args.mean_ms}ms σ={args.jitter_ms}ms 실패율={args.error_rate*100:.2f}%")
    print()

    try:
        stats = run_load_test(
            remote_ip=remote_ip,
            remote_port=remote_port,
            cps=args.cps,
            duration_sec=args.dur,
            hold_sec=args.hold,
            workers=args.workers,
        )
    finally:
        if uas:
            uas.stop()

    print_report(stats, cps=args.cps, duration_sec=args.dur)

    # CSV 저장
    if args.out:
        out_path = Path(args.out)
    else:
        ts = time.strftime("%Y%m%d_%H%M%S")
        results_dir = Path(__file__).parent.parent.parent / "results" / f"e2e_{ts}"
        out_path = results_dir / "stats.csv"

    write_stats_csv(stats, out_path)
    print(f"  stats.csv → {out_path}")

    # parse_sipp_results.py 로 SLO 재검증
    analyzer = Path(__file__).parent.parent.parent / "scripts" / "parse_sipp_results.py"
    if analyzer.exists():
        import subprocess
        subprocess.run(
            [sys.executable, str(analyzer), "--stats", str(out_path),
             "--run-id", f"e2e_{args.cps}cps_{args.dur}s",
             "--slo-p95", "500", "--slo-success", "99.9"],
            check=False,
        )

    overall = (
        stats.success_pct >= 99.9
        and stats.percentile(95) <= 500.0
    )
    return 0 if overall else 1


if __name__ == "__main__":
    sys.exit(main())
