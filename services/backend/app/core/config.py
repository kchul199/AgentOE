"""Application configuration using Pydantic Settings."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Application
    VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development")
    LOG_LEVEL: str = Field(default="INFO")
    CORS_ORIGINS: list[str] = Field(default=["http://localhost:3000"])

    # MongoDB
    MONGODB_URI: str = Field(...)
    MONGODB_DB_NAME: str = Field(default="agentoe")

    # Redis
    REDIS_URL: str = Field(...)

    # JWT
    JWT_SECRET: str = Field(...)
    JWT_ALGORITHM: str = Field(default="HS256")
    JWT_EXPIRE_MINUTES: int = Field(default=60)

    # Groq AI
    GROQ_API_KEY: str = Field(...)
    GROQ_STT_MODEL: str = Field(default="whisper-large-v3-turbo")
    GROQ_LLM_MODEL: str = Field(default="llama-4-scout-17b-16e-instruct")
    GROQ_LLM_FALLBACK_MODEL: str = Field(default="llama-3.3-70b-versatile")

    # Google TTS
    GOOGLE_APPLICATION_CREDENTIALS: str = Field(...)
    GOOGLE_TTS_LANGUAGE: str = Field(default="ko-KR")
    GOOGLE_TTS_VOICE: str = Field(default="ko-KR-Neural2-C")

    # VBGW (legacy outbound — backend → vbgw 호출용. 현재는 미사용)
    VBGW_GRPC_ENDPOINT: str = Field(default="grpc://localhost:50051")
    VBGW_WS_ENDPOINT: str = Field(default="ws://localhost:50052")

    # gRPC server (backend 가 VoicebotAiService 호스팅 — vbgw bridge 가 client)
    GRPC_ENABLED: bool = Field(default=True)
    GRPC_PORT: int = Field(default=50051)
    GRPC_MAX_CONCURRENT_STREAMS: int = Field(default=200)
    GRPC_GRACEFUL_SHUTDOWN_SEC: float = Field(default=25.0)
    GRPC_REFLECTION_ENABLED: bool = Field(default=False)  # prod=false, dev=true

    # Circuit Breaker — 공통
    CB_FAILURE_THRESHOLD: int = Field(default=5)  # 모든 서비스 공통 연속 실패 임계값
    CB_SUCCESS_THRESHOLD: int = Field(default=2)  # HALF_OPEN → CLOSED 연속 성공 횟수

    # Circuit Breaker — STT (Groq Whisper)
    CB_STT_RECOVERY_TIMEOUT: float = Field(default=30.0)
    CB_STT_HALF_OPEN_MAX_CALLS: int = Field(default=3)

    # Circuit Breaker — LLM (Groq Llama)
    CB_LLM_RECOVERY_TIMEOUT: float = Field(default=30.0)
    CB_LLM_HALF_OPEN_MAX_CALLS: int = Field(default=2)  # 보수적: Llama 응답 지연 고려

    # Circuit Breaker — TTS (Google Neural2)
    CB_TTS_RECOVERY_TIMEOUT: float = Field(default=20.0)  # 짧음: TTS 빠른 복구 목표
    CB_TTS_HALF_OPEN_MAX_CALLS: int = Field(default=3)

    # Session
    SESSION_TTL_SECONDS: int = Field(default=86400)  # 24h
    MAX_SESSIONS_PER_TENANT: int = Field(default=100)

    # ── 외부 API Timeout (전역) ──────────────────────────────────────────────
    # Latency is King: 모든 외부 I/O는 반드시 timeout 부여. 무응답 시 CB 조기 OPEN.
    HTTP_CONNECT_TIMEOUT: float = Field(default=2.0)
    HTTP_READ_TIMEOUT: float = Field(default=8.0)
    HTTP_WRITE_TIMEOUT: float = Field(default=4.0)
    HTTP_POOL_TIMEOUT: float = Field(default=2.0)
    # 서비스별 상한 (asyncio.wait_for 전역 cutoff)
    STT_TIMEOUT_SECONDS: float = Field(default=4.0)
    LLM_TIMEOUT_SECONDS: float = Field(default=6.0)
    TTS_TIMEOUT_SECONDS: float = Field(default=3.0)

    # ── PII 마스킹 ───────────────────────────────────────────────────────────
    PII_MASKING_ENABLED: bool = Field(default=True)
    # 원본 저장은 테넌트 법적 요건에 따라 명시적 opt-in. 기본값은 비활성.
    PII_RAW_PERSIST_ENABLED: bool = Field(default=False)

    # ── Data Retention (개보법/GDPR) ─────────────────────────────────────────
    SESSION_RETENTION_DAYS: int = Field(default=30)
    AUDIT_RETENTION_DAYS: int = Field(default=365)
    # 녹취 저장 기본 비활성 — 테넌트 동의/법적 근거 있을 때만 true
    AUDIO_PERSIST_ENABLED: bool = Field(default=False)
    AUDIO_RETENTION_DAYS: int = Field(default=7)

    # ── Rate Limiting ────────────────────────────────────────────────────────
    RATE_LIMIT_ENABLED: bool = Field(default=True)
    RATE_LIMIT_PER_IP_PER_MIN: int = Field(default=120)
    RATE_LIMIT_PER_TENANT_PER_MIN: int = Field(default=6000)

    # ── Graceful Shutdown ────────────────────────────────────────────────────
    SHUTDOWN_DRAIN_TIMEOUT_SECONDS: int = Field(default=30)
    SHUTDOWN_ACTIVE_CALL_ANNOUNCE: bool = Field(default=True)

    # ── Cost Guardrail ───────────────────────────────────────────────────────
    TENANT_MONTHLY_BUDGET_USD_DEFAULT: float = Field(default=0.0)  # 0 = 무제한(디폴트)

    # ── Redis Key Namespace (멀티테넌시 격리) ───────────────────────────────
    REDIS_KEY_NAMESPACE: str = Field(default="agentoe")
    # tenant prefix enforcement: True 권장, 레거시 호환 시 False
    REDIS_TENANT_SCOPED_KEYS: bool = Field(default=True)

    # ── Connection Pools ─────────────────────────────────────────────────────
    REDIS_POOL_SIZE: int = Field(default=50)
    MONGODB_POOL_SIZE: int = Field(default=100)

    # ── Agentic (Strangler Fig 롤아웃 스위치) ────────────────────────────────
    AGENTIC_DISABLED: bool = Field(default=False)
    # CSV 문자열 ("t_acme,t_foo") — 공백 허용, 빈 문자열은 무시
    AGENTIC_TENANTS: str = Field(default="")
    AGENTIC_CANARY_PERCENT: int = Field(default=0, ge=0, le=100)

    # ── WebSocket Origin 화이트리스트 (브라우저 CSRF/하이재킹 방지) ─────────
    WS_ALLOWED_ORIGINS: list[str] = Field(default=["http://localhost:3000"])
    # 비-브라우저(모바일/서버) 클라이언트는 Origin 헤더 없음 — 허용 여부
    WS_ALLOW_EMPTY_ORIGIN: bool = Field(default=True)

    # ── JWT / JWKS ───────────────────────────────────────────────────────────
    # RS256/ES256 회전 지원: JWKS URL 설정 시 kid 기반 공개키 자동 조회
    JWKS_URL: str = Field(default="")
    JWKS_CACHE_TTL_SECONDS: int = Field(default=300)
    # 강화된 토큰 검증 (iss/aud)
    JWT_ISSUER: str = Field(default="")
    JWT_AUDIENCE: str = Field(default="")

    # ── Tenant Header Validation ─────────────────────────────────────────────
    # X-Tenant-Id 헤더 ≠ JWT claim.tenant_id 인 경우 403 거부
    ENFORCE_TENANT_HEADER_MATCH: bool = Field(default=True)

    # ── LLM 토큰/비용 쿼터 (테넌트 일일) ────────────────────────────────────
    LLM_QUOTA_ENABLED: bool = Field(default=True)
    LLM_DAILY_TOKEN_QUOTA_DEFAULT: int = Field(default=5_000_000)  # 0 = 무제한
    LLM_DAILY_COST_QUOTA_CENTS_DEFAULT: int = Field(default=0)  # 0 = 무제한
    # 초과 시 동작: "fallback"(폴백 노드) | "reject"(즉시 거절) | "warn"(경고만)
    LLM_QUOTA_EXCEEDED_BEHAVIOR: str = Field(default="fallback")

    # ── Idempotency-Key 미들웨어 ─────────────────────────────────────────────
    # 클라이언트가 `Idempotency-Key` 헤더를 붙인 mutating 요청에 대해 Redis SETNX
    # 기반 중복 실행 방지. 동일 key+body → 원본 응답 재생, 동일 key/다른 body → 422.
    IDEMPOTENCY_ENABLED: bool = Field(default=True)
    IDEMPOTENCY_TTL_SECONDS: int = Field(default=600)  # 10분
    # 응답 바디 캐시 최대 크기 (byte) — 이 이상이면 key 만 기록하고 메타만 재생
    IDEMPOTENCY_MAX_BODY_BYTES: int = Field(default=256 * 1024)
    # 헤더 없이도 idempotency 를 강제할 경로 prefix (CSV). 비어 있으면 opt-in 전용.
    IDEMPOTENCY_REQUIRED_PATHS: str = Field(default="")

    # ── Phase N — 운영포탈 (Operations Portal) ─────────────────────────────
    # Alertmanager 연동 (AM proxy + am_poller)
    ALERTMANAGER_URL: str = Field(default="http://alertmanager:9093")
    ALERTMANAGER_USER: str = Field(default="")
    ALERTMANAGER_PASSWORD: str = Field(default="")
    AM_POLLER_ENABLED: bool = Field(default=True)

    # Portal CORS (서비스 도메인)
    PORTAL_ORIGIN: str = Field(default="http://localhost:4000")

    # Portal JWT (auth_portal.py — 별도 secret 로 portal 격리 강화)
    PORTAL_JWT_SECRET: str = Field(default="")  # 빈 문자열이면 JWT_SECRET 공용
    PORTAL_JWT_EXPIRE_MINUTES: int = Field(default=15)
    PORTAL_REFRESH_EXPIRE_HOURS: int = Field(default=8)
    PORTAL_MFA_ISSUER: str = Field(default="agentoe-portal")

    # MFA envelope key (N1 MVP 레거시 — N5 에서 KMS 로 격상; 두 값 공존 가능)
    PORTAL_MFA_ENVELOPE_KEY: str = Field(
        default=""
    )  # 32-byte hex, 비어 있으면 평문 base64 fallback

    # MFA KMS envelope (N5.2) — 비어 있으면 PORTAL_MFA_ENVELOPE_KEY 로 폴백
    # production 에서는 반드시 설정해야 함
    PORTAL_KMS_KEY_ID: str = Field(default="")  # AWS KMS key ARN or alias
    PORTAL_KMS_REGION: str = Field(default="ap-northeast-2")

    # ── SSE connection guard (Phase N — N1.12) ─────────────────────────────
    # 포드 당 SSE 동시 연결 상한. uvicorn --limit-concurrency 와 함께 2중 방어.
    # 0 = 비활성 (개발 환경).  prod 기본값 200 = worker×50 기준.
    SSE_MAX_CONNECTIONS_PER_POD: int = Field(default=200)

    # ── Prometheus HTTP API (Phase N — N2.1) ───────────────────────────────
    # N2 에서 인프라 마련. N3 에서 실 쿼리 연동 (PrometheusClient).
    PROMETHEUS_URL: str = Field(default="http://prometheus:9090")
    PROMETHEUS_USER: str = Field(default="")
    PROMETHEUS_PASSWORD: str = Field(default="")


settings = Settings()
