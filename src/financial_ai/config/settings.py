from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import (
    Field,
    SecretStr,
    computed_field,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict

logger = logging.getLogger(__name__)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_default=True,
    )

    # ENVIRONMENT
    APP_ENV: Literal["development", "staging", "production", "testing"] = Field(
        default="development",
        description="Runtime environment. Drives defaults for debug, logging, models.",
    )
    APP_NAME: str = Field(default="financial-ai-agent")
    APP_VERSION: str = Field(default="0.1.0")

    # DERIVED FLAGS
    DEBUG: bool = Field(
        default=False,
    )
    TESTING: bool = Field(default=False)
    MOCK_EXTERNAL_APIS: bool = Field(default=False)

    @model_validator(mode="after")
    def apply_env_defaults(self) -> Settings:
        is_dev = self.APP_ENV == "development"
        is_test = self.APP_ENV == "testing"

        if is_dev or is_test:
            object.__setattr__(self, "DEBUG", True)

        if is_test:
            object.__setattr__(self, "TESTING", True)
            object.__setattr__(self, "MOCK_EXTERNAL_APIS", True)

        return self

    # Paths - Computed
    @computed_field
    @property
    def PROJECT_ROOT(self) -> Path:
        return Path(__file__).resolve().parents[3]

    @computed_field
    @property
    def DATA_DIR(self) -> Path:
        return self.PROJECT_ROOT / "data"

    @computed_field
    @property
    def RAW_DATA_DIR(self) -> Path:
        return self.DATA_DIR / "raw"

    @computed_field
    @property
    def PROCESSED_DATA_DIR(self) -> Path:
        return self.DATA_DIR / "processed"

    @computed_field
    @property
    def VECTOR_STORE_DIR(self) -> Path:
        if self.APP_ENV == "testing":
            return Path("/tmp/finai_test/vector_store")
        return self.DATA_DIR / "vector_store"

    # API SERVER
    API_HOST: str = Field(default="0.0.0.0")
    API_PORT: int = Field(default=8000, ge=1, le=65535)
    API_WORKERS: int = Field(default=1, ge=1, description="Increase to 4+ in production")
    CORS_ORIGINS: str = Field(default="http://localhost:3000,http://localhost:8000")

    @computed_field
    @property
    def CORS_ORIGINS_LIST(self) -> list[str]:
        if not self.CORS_ORIGINS or not self.CORS_ORIGINS.strip():
            return []
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]

    # OpenAI
    OPENAI_API_KEY: SecretStr | None = Field(default=None)
    GROQ_API_KEY: SecretStr | None = Field(default=None)

    # EMBEDDING
    EMBEDDING_PROVIDER: Literal["openai", "local"] = Field(default="local")
    EMBEDDING_MODEL: str = Field(default="all-MiniLM-L6-v2")
    EMBEDDING_DIMENSIONS: int = Field(default=384)
    EMBEDDING_BATCH_SIZE: int = Field(default=100, ge=1, le=2048)

    # LLM
    LLM_PROVIDER: Literal["openai", "aanthropic", "groq", "local"] = Field(default="local")
    LLM_MODEL: str = Field(default="gpt-3.5-turbo")
    LLM_TEMPERATURE: float = Field(default=0.0, ge=0.0, le=2.0)
    LLM_MAX_TOKENS: int = Field(default=2048, ge=1)
    LLM_REQUEST_TIMEOUT: int = Field(default=60, ge=5)
    LLM_BASE_URL: str = Field(default="https://api.groq.com/openai/v1")

    # POSTGRES_DB
    POSTGRES_HOST: str = Field(default="localhost")
    POSTGRES_PORT: int = Field(default=5432, ge=1, le=65535)
    POSTGRES_USER: str = Field(default="finai")
    POSTGRES_DB: str = Field(default="financial_ai")
    POSTGRES_PASSWORD: SecretStr = Field(description="Set password in .env")

    DB_POOL_MIN_SIZE: int = Field(default=2, ge=1)
    DB_POOL_MAX_SIZE: int = Field(default=10, ge=2)
    DB_POOL_RECYCLE_SECONDS: int = Field(default=1800, ge=60)
    DB_QUERY_TIMEOUT_SECONDS: int = Field(default=30, ge=1)
    DB_CONNECT_SECONDS: int = Field(default=10, ge=1)

    @computed_field
    @property
    def DATABASE_URL(self) -> SecretStr:
        url = (
            f"postgresql+asyncpg://"
            f"{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD.get_secret_value()}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
            f"/{self.POSTGRES_DB}"
        )
        return SecretStr(url)

    @computed_field
    @property
    def DATABASE_URL_SYNC(self) -> SecretStr:
        url = (
            f"postgresql+psycopg2://"
            f"{self.POSTGRES_USER}:"
            f"{self.POSTGRES_PASSWORD.get_secret_value()}"
            f"@{self.POSTGRES_HOST}:{self.POSTGRES_PORT}"
            f"/{self.POSTGRES_DB}"
        )
        return SecretStr(url)

    # REDIS
    REDIS_HOST: str = Field(default="localhost")
    REDIS_PORT: int = Field(default=6379, ge=1, le=65535)
    REDIS_DB: int = Field(default=0, ge=0, le=15)
    REDIS_PASSWORD: SecretStr = Field(description="Set password in .env")
    REDIS_MAX_CONNECTIONS: int = Field(default=20, ge=1)
    REDIS_SOCKET_TIMEOUT_SECONDS: int = Field(default=5, ge=1)
    REDIS_DEFAULT_TTL_SECONDS: int = Field(default=3600, ge=60)
    REDIS_CONNECT_TIMEOUT_SECONDS: int = Field(default=5, ge=1, le=30)

    @computed_field
    @property
    def REDIS_URL(self) -> SecretStr:
        url = (
            f"redis://:{self.REDIS_PASSWORD.get_secret_value()}"
            f"@{self.REDIS_HOST}:{self.REDIS_PORT}"
            f"/{self.REDIS_DB}"
        )
        return SecretStr(url)

    # Processing & Retrieval
    CHUNK_SIZE_TOKENS: int = Field(default=512, ge=64, le=2048)
    CHUNK_OVERLAP_TOKENS: int = Field(default=50, ge=0, le=200)
    TOP_K_RESULTS: int = Field(default=5, ge=1, le=50)
    HYBRID_SEARCH_ALPHA: float = Field(default=0.7, ge=0.0, le=1.0)
    VECTOR_SEARCH_THRESHOLD: float = Field(default=0.3, ge=0.0, le=1)
    MAX_CONTEXT_TOKENS: int = Field(default=6000, ge=1000)

    # logging
    LOG_LEVEL: str = Field(default="INFO")
    LOG_FORMAT: Literal["json", "console"] = Field(default="console")

    @model_validator(mode="after")
    def apply_log_defaults(self) -> Settings:
        if self.APP_ENV == "production":
            if self.LOG_LEVEL == "INFO":
                object.__setattr__(self, "LOG_LEVEL", "WARNING")
            if self.LOG_FORMAT == "console":
                object.__setattr__(self, "LOG_FORMAT", "json")
        elif self.APP_ENV in ("development", "testing"):
            if self.LOG_LEVEL == "INFO":
                object.__setattr__(self, "LOG_LEVEL", "DEBUG")
        return self

    # RATE LIMITING
    RATE_LIMIT_REQUESTS: int = Field(default=100, ge=1)
    RATE_LIMIT_PERIOD_SECONDS: int = Field(default=60, ge=1)
    API_KEY_ENABLED: bool = Field(default=False)
    API_KEY: SecretStr | None = Field(default=None)

    # SEC EDGAR Ingestion
    EDGAR_USER_AGENT: str = Field(default="financial-ai-agent contact@example.com")
    EDGAR_RATE_LIMIT_RPS: int = Field(default=8, ge=1, le=10)
    EDGAR_REQUEST_TIMEOUT_SECONDS: int = Field(default=30, ge=5)
    EDGAR_MAX_RETRIES: int = Field(default=3, ge=1)

    # Validators
    @model_validator(mode="after")
    def validate_chunk_settings(self) -> Settings:
        if self.CHUNK_OVERLAP_TOKENS >= self.CHUNK_SIZE_TOKENS:
            raise ValueError(
                f"CHUNK_OVERLAP_TOKENS ({self.CHUNK_OVERLAP_TOKENS}) must be less than "
                f"CHUNK_SIZE_TOKENS ({self.CHUNK_SIZE_TOKENS})"
            )
        return self

    @model_validator(mode="after")
    def validate_startup(self) -> Settings:
        issues: list[str] = []
        is_testing = self.APP_ENV == "testing" or self.MOCK_EXTERNAL_APIS

        if not is_testing:
            if self.EMBEDDING_PROVIDER == "openai" and not self.OPENAI_API_KEY:
                issues.append(
                    "OPENAI_API_KEY is required when EMBEDDING_PROVIDER='openai. Set it in .env"
                )
            if self.LLM_PROVIDER == "openai" and not self.OPENAI_API_KEY:
                issues.append(
                    "OPENAI_API_KEY is required when LLM_PROVIDER='openai'. Set it in .env"
                )
            if self.LLM_PROVIDER == "groq" and not self.GROQ_API_KEY:
                issues.append("GROQ_API_KEY is required when LLM_PROVIDER='groq'. Set in .env")

        # production hardening
        if self.APP_ENV == "production":
            if self.CORS_ORIGINS.strip() in ("*", ""):
                issues.append(
                    "CORS_ORIGINS is '*' or empty - must be specific domains in production."
                )
            if self.DEBUG:
                issues.append("DEBUG=True in production.")
            if not self.POSTGRES_HOST or self.POSTGRES_HOST == "localhost":
                issues.append("POSTGRES_HOST is 'localhost' - use a real host in production")

        if issues:
            raise ValueError(
                "Configuration errors detected:\n" + "\n".join(f" + {issue}" for issue in issues)
            )
        return self


# Accessor - single cached instance per process


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    instance = Settings()
    logger.info(
        "Settings loaded - env=%s debug=%s embedding=%s llm=%s",
        instance.APP_ENV,
        instance.DEBUG,
        instance.EMBEDDING_MODEL,
        instance.LLM_MODEL,
    )
    return instance
