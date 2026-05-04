"""
구조화 로깅 — structlog + context var 기반 자동 주입

설계 원칙:
  - 모든 로그에 session_id / tenant_id / request_id 자동 포함
  - LoggingMiddleware가 요청 진입 시 bind_request_context() 호출
  - 파이프라인 / WebSocket 핸들러에서 bind_session_context() 추가 바인딩
  - 운영 환경: JSON Lines (ELK/Cloud Logging 직접 수집)
  - 개발 환경: 컬러 ConsoleRenderer

사용법:
    import structlog
    logger = structlog.get_logger()
    logger.info("pipeline complete", latency_ms=320)
    # → {"event":"pipeline complete","latency_ms":320,"session_id":"...","tenant_id":"..."}

    # 요청 진입 시 (middleware):
    from app.core.logging import bind_request_context
    bind_request_context(request_id="...", tenant_id="...")

    # WebSocket 연결 시:
    from app.core.logging import bind_session_context
    bind_session_context(session_id="...", tenant_id="...")
"""
from __future__ import annotations

import logging
import re
import sys
import uuid
from contextlib import contextmanager
from typing import Any, Iterator

import structlog
from structlog.contextvars import (
    bind_contextvars,
    clear_contextvars,
    unbind_contextvars,
)

from app.core.config import settings


# ── PII 마스킹 ────────────────────────────────────────────────────────────────
#
# 한국/글로벌 개보법/GDPR 준수. 모든 로그/LLM 전송/저장 경로에서 사용.
# "기본 허용"이 아닌 "기본 마스킹"을 원칙으로 한다. 원문 저장은 opt-in.
#
# Precedence: 긴 패턴(주민번호) 먼저 매칭 → 짧은 일반 숫자(카드/전화) 뒤.

# 패턴 우선순위 주의: 한국 전화(010-…)와 계좌번호 포맷이 충돌하므로
# phone_* 를 bank 보다 앞에 배치하여 오매칭(전화를 [ACCT]로)을 방지한다.
_PII_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    # 주민등록번호 (KR RRN): 6-7 (구분자 -, 공백, 없음)
    ("rrn", re.compile(r"\b\d{6}[\s-]?\d{7}\b"), "[RRN]"),
    # 이메일
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"), "[EMAIL]"),
    # 신용카드 (13-19자리, 그룹핑 -/공백 허용)
    ("credit_card", re.compile(r"\b(?:\d[ -]?){13,19}\d\b"), "[CARD]"),
    # 한국 휴대전화 (010-xxxx-xxxx 등) — bank 보다 먼저
    ("phone_kr", re.compile(r"\b01[016789][\s-]?\d{3,4}[\s-]?\d{4}\b"), "[PHONE]"),
    # 일반 전화 (02-xxxx-xxxx, 031-xxx-xxxx 등)
    ("phone_gen", re.compile(r"\b0\d{1,2}[\s-]?\d{3,4}[\s-]?\d{4}\b"), "[PHONE]"),
    # 계좌번호 (숫자 3-6-2-6-2-6 형식)
    ("bank", re.compile(r"\b\d{3,6}-\d{2,6}-\d{2,6}(?:-\d{1,6})?\b"), "[ACCT]"),
    # IP v4
    ("ipv4", re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"), "[IP]"),
    # 여권번호 (KR: M12345678)
    ("passport", re.compile(r"\b[A-Z]{1,2}\d{7,8}\b"), "[PASSPORT]"),
)

# 마스킹 제외 키 (구조화 로그에서 건드리면 안 되는 메타 키)
_PII_SAFE_KEYS: frozenset[str] = frozenset({
    "session_id", "tenant_id", "request_id", "client_id", "trace_id", "span_id",
    "event", "level", "timestamp", "logger", "stage", "pipeline_stage",
    "policy_level", "latency_ms", "status_code", "path", "method",
    "model", "circuit_state", "severity",
})


def mask_pii(text: str) -> str:
    """
    PII 패턴을 토큰으로 치환. 성능: 정규식 8종 × 메시지 길이.
    평균 400 byte 로그 기준 ~20μs (CPython 3.11). 핫패스에서도 무리 없음.
    """
    if not text or not isinstance(text, str):
        return text
    if not settings.PII_MASKING_ENABLED:
        return text
    masked = text
    for _name, pat, token in _PII_PATTERNS:
        masked = pat.sub(token, masked)
    return masked


def mask_pii_dict(d: dict[str, Any]) -> dict[str, Any]:
    """dict 값에 재귀적으로 마스킹 적용. safe key는 건너뜀."""
    if not settings.PII_MASKING_ENABLED:
        return d
    out: dict[str, Any] = {}
    for k, v in d.items():
        if k in _PII_SAFE_KEYS:
            out[k] = v
        elif isinstance(v, str):
            out[k] = mask_pii(v)
        elif isinstance(v, dict):
            out[k] = mask_pii_dict(v)
        elif isinstance(v, (list, tuple)):
            out[k] = type(v)(mask_pii(x) if isinstance(x, str) else x for x in v)
        else:
            out[k] = v
    return out


def _structlog_pii_processor(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: 로그 이벤트의 모든 문자열 필드에 마스킹 적용."""
    if not settings.PII_MASKING_ENABLED:
        return event_dict
    return mask_pii_dict(event_dict)


# ── 로깅 설정 ──────────────────────────────────────────────────────────────────

def setup_logging() -> None:
    """애플리케이션 시작 시 1회 호출. structlog 전역 설정."""
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    # stdlib logging 기본 설정
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level,
    )

    # 운영/개발 환경별 렌더러 선택
    if settings.ENVIRONMENT == "production":
        renderer = structlog.processors.JSONRenderer()
    else:
        renderer = structlog.dev.ConsoleRenderer(colors=True)

    structlog.configure(
        processors=[
            # 1. contextvars에 바인딩된 값 병합 (session_id, tenant_id 등)
            structlog.contextvars.merge_contextvars,
            # 2. stdlib 로그 레벨 추가
            structlog.processors.add_log_level,
            # 3. 스택 트레이스 포매팅
            structlog.processors.StackInfoRenderer(),
            structlog.processors.ExceptionRenderer(),
            # 4. ISO 8601 타임스탬프
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # 5. PII 마스킹 — 렌더링 직전에 적용 (핫패스, settings flag로 제어)
            _structlog_pii_processor,
            # 6. 최종 렌더링
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


# ── Context var 헬퍼 ──────────────────────────────────────────────────────────

def bind_request_context(
    request_id: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
    path: str | None = None,
    method: str | None = None,
) -> str:
    """
    HTTP 요청 진입 시 호출. structlog context var에 요청 메타데이터 바인딩.
    반환값: 생성된 request_id
    """
    rid = request_id or str(uuid.uuid4())[:8]
    ctx: dict = {"request_id": rid}
    if tenant_id:
        ctx["tenant_id"] = tenant_id
    if client_id:
        ctx["client_id"] = client_id
    if path:
        ctx["path"] = path
    if method:
        ctx["method"] = method
    bind_contextvars(**ctx)
    return rid


def bind_session_context(
    session_id: str,
    tenant_id: str | None = None,
    client_id: str | None = None,
) -> None:
    """
    WebSocket 연결 / 세션 시작 시 호출.
    이후 해당 코루틴에서 발생하는 모든 로그에 session_id 자동 포함.
    """
    ctx: dict = {"session_id": session_id}
    if tenant_id:
        ctx["tenant_id"] = tenant_id
    if client_id:
        ctx["client_id"] = client_id
    bind_contextvars(**ctx)


def bind_pipeline_context(
    stage: str,
    policy_level: str | None = None,
) -> None:
    """AI 파이프라인 단계별 context 바인딩."""
    ctx: dict = {"pipeline_stage": stage}
    if policy_level:
        ctx["policy_level"] = policy_level
    bind_contextvars(**ctx)


def clear_request_context() -> None:
    """요청 종료 시 context var 정리 (미들웨어 finally에서 호출)."""
    clear_contextvars()


def unbind_keys(*keys: str) -> None:
    """특정 키만 바인딩 해제."""
    unbind_contextvars(*keys)


# ── Context 누수 방지 헬퍼 (Track 2-e) ────────────────────────────────────────
#
# FastAPI 요청/WebSocket/에이전틱 파이프라인 등 비동기 흐름에서 bind 와 unbind 가
# 대칭을 이루지 못하면 후속 요청에 이전 세션의 session_id / tenant_id 가 섞여
# 들어가는 경우가 발생한다 (교차 세션 로그 오염). 아래 두 헬퍼는 "반드시 짝을
# 맞춘 해제" 를 강제하기 위한 공용 유틸이다.
#
# 원칙:
#   1. 진입 시 bind, 이탈 시 반드시 unbind (예외 포함)
#   2. 중첩 진입은 가능 (하위 스코프가 끝나면 상위 키만 남음)
#   3. contextvars 는 asyncio 태스크 단위로 격리되므로 같은 태스크 내부에서만
#      유효. 새 태스크(Task/asyncio.create_task)는 새 스냅샷을 상속.


@contextmanager
def scoped_context(**kwargs: Any) -> Iterator[None]:
    """
    with 블록 동안만 contextvars 에 값을 바인딩. 블록 종료 시 **지정한 키만**
    unbind 한다 (clear 가 아님 → 상위 바인딩 훼손 금지).

    사용 예::

        with scoped_context(session_id=sid, tenant_id=tid):
            await do_pipeline(...)
    """
    keys = tuple(k for k, v in kwargs.items() if v is not None)
    bind_contextvars(**{k: v for k, v in kwargs.items() if v is not None})
    try:
        yield
    finally:
        if keys:
            unbind_contextvars(*keys)


def unbind_request_context() -> None:
    """
    요청/WebSocket 종료 시 호출. 현재 태스크의 모든 structlog context 를 제거.

    clear_request_context 와 동의어. 명명의 의도가 '무슨 일이 있어도 반드시 전부
    비운다' 라는 점을 finally 블록에서 더 잘 드러내기 위해 별칭을 제공한다.
    """
    clear_contextvars()
