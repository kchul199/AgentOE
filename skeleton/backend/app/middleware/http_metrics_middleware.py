"""
HTTP Metrics Middleware

SLO 측정용 4개 시리즈를 노출한다:

  http_requests_total{method, route, status}            — counter
  http_request_duration_seconds{method, route}          — histogram
  http_requests_in_flight{route}                        — gauge
  http_request_size_bytes / response_size_bytes         — histogram (optional)

설계 포인트:
  1. **라벨 카디널리티 보호** — route 는 FastAPI 의 path template ("/api/v1/sessions/{id}")
     을 사용. 실제 path 는 절대 라벨에 박지 않음.
  2. **존재하지 않는 path** 는 route="UNKNOWN" 으로 통합 — 잘못된 trafficker 가
     랜덤 경로 때려도 시리즈 폭발 안 함.
  3. **/metrics, /api/v1/livez, /readyz** 는 SLO 측정에서 제외 — 운영 트래픽.
  4. prometheus_client 미설치 환경 (개발) 에서는 no-op — import 실패 안 남.
  5. WebSocket upgrade (/api/v1/ws/*) 는 별도 처리 — 여기서 timing 잡으면 의미 없음.
"""
from __future__ import annotations

import time
from typing import Final

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.types import ASGIApp

# prometheus_client 는 선택적 의존성 (metrics.py 와 동일 정책)
try:
    from prometheus_client import Counter, Gauge, Histogram

    _HTTP_REQUESTS = Counter(
        "http_requests_total",
        "HTTP request count by method, route template, and status code",
        ["method", "route", "status"],
    )
    _HTTP_DURATION = Histogram(
        "http_request_duration_seconds",
        "HTTP request handler duration in seconds",
        ["method", "route"],
        # SLO 임계 (0.5s) 가 정확히 버킷 경계에 들어가도록 — ratio 분자 계산 무손실
        buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0),
    )
    _HTTP_IN_FLIGHT = Gauge(
        "http_requests_in_flight",
        "Number of in-progress HTTP requests by route",
        ["route"],
    )
    _PROMETHEUS_OK = True
except ImportError:  # pragma: no cover
    _PROMETHEUS_OK = False
    _HTTP_REQUESTS = _HTTP_DURATION = _HTTP_IN_FLIGHT = None  # type: ignore[assignment]


# 측정 제외 경로 — operational, SLO 분모 오염 금지.
_SKIP_PATHS: Final[frozenset[str]] = frozenset({
    "/api/v1/livez",
    "/api/v1/readyz",
    "/api/v1/livez/drain",
    "/api/v1/metrics/prometheus",
    "/metrics",
    "/api/v1/health",
})

# WebSocket 경로 prefix — 별도 measurement 가 더 의미 있음 (active_sessions Gauge 가 이미 있음)
_WS_PREFIX = "/api/v1/ws"


def _resolve_route_template(request: Request) -> str:
    """
    실제 매칭된 라우트의 path template 반환. 매칭 실패 시 'UNKNOWN'.

    FastAPI/Starlette 는 endpoint resolve 후 `request.scope["route"].path` 에
    template 이 들어 있다. 이를 사용해 path param (e.g. /sessions/{id}) 를 살린다.
    """
    route = request.scope.get("route")
    if route is None or not hasattr(route, "path"):
        return "UNKNOWN"
    return getattr(route, "path", "UNKNOWN") or "UNKNOWN"


class HTTPMetricsMiddleware(BaseHTTPMiddleware):
    """
    BaseHTTPMiddleware 위에 얹어 latency/count/in_flight 를 동시 기록.

    주의: BaseHTTPMiddleware 는 dispatch 가 끝나야 route 매칭이 완료되므로,
    in_flight inc/dec 시점은 path 기반 (UNKNOWN 가능), label 기록은 dispatch 후
    template 으로 정확히 갱신한다.
    """

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        path = request.url.path

        # 비측정 경로는 통과 — overhead 0
        if path in _SKIP_PATHS or path.startswith(_WS_PREFIX) or not _PROMETHEUS_OK:
            return await call_next(request)

        method = request.method

        # in_flight: route template 미해결 단계 — provisional 라벨 사용 후 finally 에서 정정 X
        # (Gauge 라벨 변경은 비싸므로 final route 로 한 번만 기록)
        start = time.perf_counter()
        response: Response | None = None
        try:
            response = await call_next(request)
            return response
        finally:
            elapsed = time.perf_counter() - start
            route = _resolve_route_template(request)
            status = str(response.status_code if response is not None else 500)
            try:
                _HTTP_REQUESTS.labels(method=method, route=route, status=status).inc()
                _HTTP_DURATION.labels(method=method, route=route).observe(elapsed)
            except Exception:  # noqa: BLE001
                # 메트릭 기록 실패가 요청 자체를 깨선 안 됨.
                pass
