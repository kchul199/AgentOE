"""외부 I/O timeout 표준 헬퍼.

목적:
  - 모든 외부 API 호출에 일관된 timeout을 강제 (Groq, Google TTS, VBGW 등)
  - asyncio.wait_for + httpx.Timeout 조합으로 '절대 무응답' 세션 제거
  - timeout 초과를 전용 예외(ExternalTimeoutError)로 승격 → Circuit Breaker가 포착

사용법::

    from app.core.timeouts import with_timeout, http_timeout, SVC_LLM

    async with httpx.AsyncClient(timeout=http_timeout()) as client:
        ...

    result = await with_timeout(
        call_llm(prompt), service=SVC_LLM
    )

Circuit Breaker는 ExternalTimeoutError를 failure로 계산해야 한다.
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Literal

try:
    import httpx
except ImportError:  # httpx 없을 때도 import는 통과 (테스트)
    httpx = None  # type: ignore

from app.core.config import settings

ServiceName = Literal["stt", "llm", "tts", "default"]

SVC_STT: ServiceName = "stt"
SVC_LLM: ServiceName = "llm"
SVC_TTS: ServiceName = "tts"
SVC_DEFAULT: ServiceName = "default"


class ExternalTimeoutError(Exception):
    """외부 서비스 호출이 cutoff를 초과함. CB failure로 간주."""

    def __init__(self, service: str, timeout_s: float) -> None:
        super().__init__(f"{service} timeout after {timeout_s:.2f}s")
        self.service = service
        self.timeout_s = timeout_s


def _timeout_for(service: ServiceName) -> float:
    if service == SVC_STT:
        return settings.STT_TIMEOUT_SECONDS
    if service == SVC_LLM:
        return settings.LLM_TIMEOUT_SECONDS
    if service == SVC_TTS:
        return settings.TTS_TIMEOUT_SECONDS
    return settings.HTTP_READ_TIMEOUT


async def with_timeout(
    coro: Awaitable[Any],
    *,
    service: ServiceName = SVC_DEFAULT,
    override_s: float | None = None,
) -> Any:
    """coroutine을 service cutoff로 감싼다. 초과 시 ExternalTimeoutError."""
    t = override_s if override_s is not None else _timeout_for(service)
    try:
        return await asyncio.wait_for(coro, timeout=t)
    except asyncio.TimeoutError as e:
        raise ExternalTimeoutError(service, t) from e


def http_timeout(service: ServiceName = SVC_DEFAULT) -> "httpx.Timeout":
    """
    httpx.Timeout 헬퍼 — connect/read/write/pool을 config에서 읽어온다.
    서비스별 read cutoff도 반영하여 커넥션/전체 상한이 일관되게 동작.
    """
    if httpx is None:  # pragma: no cover
        raise RuntimeError("httpx not installed")
    read = _timeout_for(service) if service != SVC_DEFAULT else settings.HTTP_READ_TIMEOUT
    return httpx.Timeout(
        connect=settings.HTTP_CONNECT_TIMEOUT,
        read=read,
        write=settings.HTTP_WRITE_TIMEOUT,
        pool=settings.HTTP_POOL_TIMEOUT,
    )
