"""실시간 모니터링 라우터 — CCU, 지연, 에러율, SLO"""
from __future__ import annotations
import random
import math
from datetime import datetime, timedelta
from fastapi import APIRouter

from app.models import MetricSnapshot, MetricHistory, TimePoint

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


def _now_iso() -> str:
    return datetime.utcnow().isoformat(timespec="seconds") + "Z"


def _rand_metric() -> MetricSnapshot:
    """재현 가능한 범위에서 현실감 있는 목 지표 생성"""
    ccu = random.randint(12, 48)
    p95 = random.randint(820, 1250)
    return MetricSnapshot(
        ts=_now_iso(),
        ccu=ccu,
        p95_ms=p95,
        p99_ms=p95 + random.randint(80, 300),
        error_rate_pct=round(random.uniform(0.3, 2.8), 2),
        slo_achieved_pct=round(random.uniform(97.5, 99.9), 2),
        stt_p95_ms=random.randint(180, 380),
        llm_p95_ms=random.randint(420, 750),
        tts_p95_ms=random.randint(90, 180),
        total_calls_today=random.randint(800, 1400),
        failed_calls_today=random.randint(5, 35),
    )


def _history(points: int = 30) -> MetricHistory:
    now = datetime.utcnow()
    ccu_pts, p95_pts, err_pts = [], [], []
    for i in range(points):
        ts = (now - timedelta(minutes=(points - i) * 2)).isoformat(timespec="seconds") + "Z"
        # 시각적으로 자연스러운 파형
        base_ccu = 28 + 12 * math.sin(i * 0.4) + random.uniform(-3, 3)
        ccu_pts.append(TimePoint(ts=ts, value=round(max(0, base_ccu), 1)))
        p95_pts.append(TimePoint(ts=ts, value=round(950 + 180 * math.sin(i * 0.3) + random.uniform(-40, 40), 0)))
        err_pts.append(TimePoint(ts=ts, value=round(max(0, 1.2 + 0.8 * math.sin(i * 0.5) + random.uniform(-0.3, 0.3)), 2)))
    return MetricHistory(ccu=ccu_pts, p95=p95_pts, error_rate=err_pts)


@router.get("/metrics", response_model=MetricSnapshot)
async def get_metrics() -> MetricSnapshot:
    """현재 시점 지표 스냅샷"""
    return _rand_metric()


@router.get("/history", response_model=MetricHistory)
async def get_history(points: int = 30) -> MetricHistory:
    """최근 N 포인트 시계열 (2분 간격)"""
    return _history(min(points, 120))
