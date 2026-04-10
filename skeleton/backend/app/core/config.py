"""Application configuration using Pydantic Settings."""
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

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

    # VBGW
    VBGW_GRPC_ENDPOINT: str = Field(default="grpc://localhost:50051")
    VBGW_WS_ENDPOINT: str = Field(default="ws://localhost:50052")

    # Circuit Breaker
    CB_FAILURE_THRESHOLD: int = Field(default=5)
    CB_RECOVERY_TIMEOUT: int = Field(default=30)
    CB_HALF_OPEN_MAX_CALLS: int = Field(default=3)

    # Session
    SESSION_TTL_SECONDS: int = Field(default=86400)  # 24h
    MAX_SESSIONS_PER_TENANT: int = Field(default=100)


settings = Settings()
