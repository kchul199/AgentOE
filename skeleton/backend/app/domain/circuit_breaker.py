"""
Circuit Breaker — CLOSED / OPEN / HALF_OPEN 상태 머신
AI 외부 서비스(STT, LLM, TTS) 장애 감지 및 자동 복구
"""
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
    failure_threshold: int = 5        # OPEN 전환 연속 실패 횟수
    recovery_timeout: float = 30.0    # OPEN → HALF_OPEN 대기 시간(초)
    half_open_max_calls: int = 3      # HALF_OPEN 상태 허용 호출 수
    success_threshold: int = 2        # HALF_OPEN → CLOSED 전환 연속 성공 횟수


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
    사용 예:
        cb = CircuitBreaker("groq-stt")
        result = await cb.call(groq_client.transcribe, audio_data)
    """

    def __init__(self, service_name: str, config: CircuitBreakerConfig | None = None):
        self.service_name = service_name
        self.config = config or CircuitBreakerConfig()
        self._stats = CircuitBreakerStats()
        self._lock = asyncio.Lock()

    @property
    def state(self) -> CircuitState:
        return self._stats.state

    @property
    def stats(self) -> CircuitBreakerStats:
        return self._stats

    async def call(self, func: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """Circuit Breaker를 통해 함수 호출"""
        async with self._lock:
            await self._check_state()
            self._stats.total_calls += 1

        try:
            result = await func(*args, **kwargs)
            await self._on_success()
            return result
        except Exception as exc:
            await self._on_failure(exc)
            raise

    async def _check_state(self) -> None:
        stats = self._stats
        if stats.state == CircuitState.OPEN:
            elapsed = time.monotonic() - stats.last_failure_time
            if elapsed >= self.config.recovery_timeout:
                logger.info(
                    "Circuit breaker transitioning OPEN → HALF_OPEN",
                    extra={"service": self.service_name}
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
                        extra={"service": self.service_name}
                    )
                    stats.state = CircuitState.CLOSED
                    stats.success_count = 0
                    stats.half_open_calls = 0

    async def _on_failure(self, exc: Exception) -> None:
        async with self._lock:
            stats = self._stats
            stats.failure_count += 1
            stats.total_failures += 1
            stats.last_failure_time = time.monotonic()

            if stats.state == CircuitState.HALF_OPEN:
                logger.warning(
                    "Circuit breaker HALF_OPEN → OPEN (recovery failed)",
                    extra={"service": self.service_name, "error": str(exc)}
                )
                stats.state = CircuitState.OPEN
                stats.half_open_calls = 0
            elif (stats.state == CircuitState.CLOSED and
                  stats.failure_count >= self.config.failure_threshold):
                logger.error(
                    "Circuit breaker CLOSED → OPEN (threshold exceeded)",
                    extra={"service": self.service_name,
                           "failures": stats.failure_count}
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

_registry: dict[str, CircuitBreaker] = {}


def get_circuit_breaker(
    service_name: str,
    config: CircuitBreakerConfig | None = None,
) -> CircuitBreaker:
    """서비스명으로 Circuit Breaker 싱글턴 반환"""
    if service_name not in _registry:
        _registry[service_name] = CircuitBreaker(service_name, config)
    return _registry[service_name]


def get_all_statuses() -> list[dict]:
    return [cb.get_status() for cb in _registry.values()]
