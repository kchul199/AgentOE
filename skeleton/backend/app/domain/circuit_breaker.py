"""
Circuit Breaker — CLOSED / OPEN / HALF_OPEN 상태 머신
AI 외부 서비스(STT, LLM, TTS) 장애 감지 및 자동 복구

설계 결정:
  1. In-Process 싱글턴:
     이 서비스는 WebSocket 기반 세션 고정(sticky) 아키텍처입니다.
     한 WS 커넥션은 수명 동안 동일 Pod에 고정되므로, Pod마다 독립적으로
     외부 서비스 건강을 판단하는 In-Process CB로 충분합니다.
     분산 CB(Redis 기반)는 stateless REST API 라운드로빈 환경에서 필요하며,
     현재 아키텍처에는 불필요합니다.

  2. Lock Race Window (의도된 트레이드오프):
     `call()` / `__aenter__`는 Lock 획득 → 상태 체크 → total_calls 증가 → Lock 해제
     순서로 동작합니다. Lock 해제 후 실제 I/O 호출 사이에 다른 코루틴이
     CLOSED → OPEN 전환을 완료하면 1회 여분 호출이 발생할 수 있습니다.
     이는 STT/LLM I/O 시간(수백 ms) 동안 Lock을 점유하는 방식보다
     이벤트 루프 처리량이 훨씬 높으므로 허용된 트레이드오프입니다.

  3. 예외 선별 (excluded_exceptions):
     CircuitBreakerConfig.excluded_exceptions에 지정된 예외 타입은
     failure_count 에 포함되지 않습니다.
     - RuntimeError: 패키지 미설치·설정 누락 등 인프라 오류 — 외부 서비스 건강과 무관
     - HTTP 4xx 에러 (잘못된 요청): 우리 쪽 버그 — 외부 서비스 건강과 무관
     asyncio.CancelledError는 BaseException 상속(Python 3.8+)이므로
     `except Exception`에 잡히지 않아 자동으로 카운트에서 제외됩니다.

  4. 컨텍스트 매니저 (권장):
     `async with cb:` 패턴으로 불필요한 wrapper closure를 제거합니다.
     `call()` 메서드는 하위 호환성을 위해 유지하며 컨텍스트 매니저에 위임합니다.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class CircuitState(str, Enum):
    CLOSED = "CLOSED"       # 정상 동작
    OPEN = "OPEN"           # 장애 감지 — 즉시 거부
    HALF_OPEN = "HALF_OPEN" # 복구 시도 중


@dataclass
class CircuitBreakerConfig:
    failure_threshold: int = 5         # OPEN 전환 연속 실패 횟수
    recovery_timeout: float = 30.0     # OPEN → HALF_OPEN 대기 시간(초)
    half_open_max_calls: int = 3       # HALF_OPEN 상태 허용 동시 호출 수
    success_threshold: int = 2         # HALF_OPEN → CLOSED 전환 연속 성공 횟수
    # 외부 서비스 건강과 무관한 예외 타입 — failure_count에서 제외됨
    # 예: RuntimeError("groq not installed"), ValueError (잘못된 입력 포맷)
    excluded_exceptions: tuple[type[Exception], ...] = field(default_factory=tuple)


@dataclass
class CircuitBreakerStats:
    state: CircuitState = CircuitState.CLOSED
    failure_count: int = 0
    success_count: int = 0
    last_failure_time: float = 0.0
    total_calls: int = 0
    total_failures: int = 0
    half_open_calls: int = 0


class CircuitBreakerOpenError(Exception):
    """Circuit Breaker OPEN 상태에서 호출 시 발생"""
    def __init__(self, service_name: str):
        super().__init__(f"Circuit breaker OPEN for service '{service_name}'")
        self.service_name = service_name


class CircuitBreaker:
    """
    비동기 Circuit Breaker.

    권장 사용 패턴 (컨텍스트 매니저):
        async with cb:
            result = await external_service.call(...)
        # result는 블록 후에도 접근 가능; 예외 발생 시 블록 탈출

    하위 호환 사용 패턴 (call 메서드):
        result = await cb.call(external_service.call, arg1, arg2)
    """

    def __init__(self, service_name: str, config: CircuitBreakerConfig | None = None):
        self.service_name = service_name
        self.config = config or CircuitBreakerConfig()
        self._stats = CircuitBreakerStats()
        self._lock = asyncio.Lock()

    # ── 프로퍼티 ─────────────────────────────────────────────────────────────

    @property
    def state(self) -> CircuitState:
        return self._stats.state

    @property
    def stats(self) -> CircuitBreakerStats:
        return self._stats

    # ── 컨텍스트 매니저 (권장) ───────────────────────────────────────────────

    async def __aenter__(self) -> "CircuitBreaker":
        """Lock 획득 → 상태 체크 → total_calls 증가 → Lock 해제.
        Lock은 상태 체크 후 즉시 해제됩니다(설계 결정 #2 참조).
        """
        async with self._lock:
            await self._check_state()
            self._stats.total_calls += 1
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: Any,
    ) -> bool:
        """성공/실패 처리. 예외는 항상 전파(return False).
        CircuitBreakerOpenError는 _on_failure를 호출하지 않습니다.
        excluded_exceptions는 failure_count에 포함하지 않습니다.
        """
        if exc_type is None:
            await self._on_success()
        elif exc_val is not None and not isinstance(exc_val, CircuitBreakerOpenError):
            if not self._is_excluded(exc_val):
                await self._on_failure(exc_val)
        return False  # 예외 억제 안 함 — 항상 호출자로 전파

    # ── call() — 하위 호환 메서드 ────────────────────────────────────────────

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Circuit Breaker를 통해 함수 호출. 내부적으로 컨텍스트 매니저에 위임."""
        async with self:
            return await func(*args, **kwargs)

    # ── 내부 헬퍼 ────────────────────────────────────────────────────────────

    def _is_excluded(self, exc: BaseException) -> bool:
        """excluded_exceptions에 해당하면 True — failure_count 제외 대상."""
        if not self.config.excluded_exceptions:
            return False
        return isinstance(exc, self.config.excluded_exceptions)

    async def _check_state(self) -> None:
        """Lock 내부에서 호출. OPEN/HALF_OPEN 상태에 따라 진입 제어."""
        stats = self._stats
        if stats.state == CircuitState.OPEN:
            elapsed = time.monotonic() - stats.last_failure_time
            if elapsed >= self.config.recovery_timeout:
                logger.info(
                    "Circuit breaker OPEN → HALF_OPEN",
                    extra={"service": self.service_name, "elapsed_s": round(elapsed, 1)},
                )
                stats.state = CircuitState.HALF_OPEN
                stats.half_open_calls = 0
                stats.success_count = 0
            else:
                raise CircuitBreakerOpenError(self.service_name)

        if stats.state == CircuitState.HALF_OPEN:
            if stats.half_open_calls >= self.config.half_open_max_calls:
                raise CircuitBreakerOpenError(self.service_name)
            stats.half_open_calls += 1

    async def _on_success(self) -> None:
        async with self._lock:
            stats = self._stats
            stats.failure_count = 0
            if stats.state == CircuitState.HALF_OPEN:
                stats.success_count += 1
                if stats.success_count >= self.config.success_threshold:
                    logger.info(
                        "Circuit breaker HALF_OPEN → CLOSED (recovered)",
                        extra={"service": self.service_name},
                    )
                    stats.state = CircuitState.CLOSED
                    stats.success_count = 0
                    stats.half_open_calls = 0

    async def _on_failure(self, exc: BaseException) -> None:
        async with self._lock:
            stats = self._stats
            stats.failure_count += 1
            stats.total_failures += 1
            stats.last_failure_time = time.monotonic()

            if stats.state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Circuit breaker HALF_OPEN → OPEN (recovery failed)",
                    extra={"service": self.service_name, "error": str(exc)},
                )
                stats.state = CircuitState.OPEN
                stats.half_open_calls = 0
            elif (
                stats.state == CircuitState.CLOSED
                and stats.failure_count >= self.config.failure_threshold
            ):
                logger.error(
                    "Circuit breaker CLOSED → OPEN (threshold exceeded)",
                    extra={"service": self.service_name, "failures": stats.failure_count},
                )
                stats.state = CircuitState.OPEN

    def reset(self) -> None:
        """수동 리셋 (운영팀 개입 시 사용)"""
        self._stats = CircuitBreakerStats()
        logger.info("Circuit breaker manually reset", extra={"service": self.service_name})

    def get_status(self) -> dict:
        s = self._stats
        return {
            "service": self.service_name,
            "state": s.state.value,
            "failure_count": s.failure_count,
            "total_calls": s.total_calls,
            "total_failures": s.total_failures,
            "last_failure_time": s.last_failure_time,
            "error_rate": round(s.total_failures / max(s.total_calls, 1), 4),
        }


# ── 전역 Circuit Breaker 레지스트리 ──────────────────────────────────────────
# In-Process 싱글턴 — WS 세션이 특정 Pod에 고정되므로 Pod 독립 CB로 충분합니다.
# 관제 대시보드에서 /api/v1/status를 조회할 때 Pod별로 CB 상태가 다를 수 있음을
# 인지하고 있어야 합니다.

_registry: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    service_name: str,
    config: CircuitBreakerConfig | None = None,
) -> CircuitBreaker:
    """서비스명으로 Circuit Breaker 싱글턴 반환.
    최초 호출 시에만 config가 적용되며, 이후 호출은 캐시된 인스턴스를 반환합니다.
    """
    if service_name not in _registry:
        _registry[service_name] = CircuitBreaker(service_name, config)
    return _registry[service_name]


def make_service_config(service_key: str) -> CircuitBreakerConfig:
    """settings에서 서비스별 CB 파라미터를 읽어 CircuitBreakerConfig를 생성합니다.
    각 서비스 __init__ 시점에 한 번만 호출됩니다(settings가 이미 로드된 상태).

    excluded_exceptions 기본값:
      - RuntimeError: 패키지 미설치·환경 설정 오류 — 외부 서비스 건강과 무관
    추가 제외 예외가 필요하면 서비스 파일에서 config를 직접 구성하세요.
    """
    from app.core.config import settings

    common: dict = {
        "failure_threshold": settings.CB_FAILURE_THRESHOLD,
        "success_threshold": settings.CB_SUCCESS_THRESHOLD,
        "excluded_exceptions": (RuntimeError,),
    }

    if service_key == "groq-stt":
        return CircuitBreakerConfig(
            **common,
            recovery_timeout=settings.CB_STT_RECOVERY_TIMEOUT,
            half_open_max_calls=settings.CB_STT_HALF_OPEN_MAX_CALLS,
        )
    if service_key in ("groq-llm-primary", "groq-llm-fallback"):
        return CircuitBreakerConfig(
            **common,
            recovery_timeout=settings.CB_LLM_RECOVERY_TIMEOUT,
            half_open_max_calls=settings.CB_LLM_HALF_OPEN_MAX_CALLS,
        )
    if service_key == "google-tts":
        return CircuitBreakerConfig(
            **common,
            recovery_timeout=settings.CB_TTS_RECOVERY_TIMEOUT,
            half_open_max_calls=settings.CB_TTS_HALF_OPEN_MAX_CALLS,
        )
    # 미등록 서비스: 공통 defaults 적용
    return CircuitBreakerConfig(**common)


def get_all_statuses() -> list[dict]:
    """모든 등록된 CB 상태 반환. 관제 API용."""
    return [cb.get_status() for cb in _registry.values()]
