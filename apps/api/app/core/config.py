import json
from pathlib import Path
from typing import Annotated
from urllib.parse import urlparse

from pydantic import AliasChoices, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ATS"
    environment: str = Field(
        default="development",
        validation_alias=AliasChoices("APP_ENV", "ENVIRONMENT"),
    )
    database_url: str
    jwt_secret_key: str
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 30
    email_verification_token_expire_hours: int = 24
    password_reset_token_expire_hours: int = 1
    max_resume_size_bytes: int = 5 * 1024 * 1024
    private_upload_dir: Path = Path("var/private_uploads")
    resume_storage_backend: str = "local"
    resume_signed_url_expire_seconds: int = 300
    redis_url: str = "redis://localhost:6379/0"
    resume_processing_queue_name: str = "hireflow:resume-processing"
    resume_processing_max_retries: int = 3
    resume_processing_retry_base_seconds: int = 5
    resume_processing_retry_max_seconds: int = 60
    r2_endpoint_url: str | None = None
    r2_bucket_name: str | None = None
    r2_access_key_id: str | None = None
    r2_secret_access_key: str | None = None
    r2_region_name: str = "auto"
    cors_allowed_origins: Annotated[list[str], NoDecode] = Field(default_factory=list)
    cors_allow_credentials: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )

    @field_validator("cors_allowed_origins", mode="before")
    @classmethod
    def _decode_origins(cls, value: object) -> object:
        # Accept JSON arrays as well as plain comma-separated values, so
        # CORS_ALLOWED_ORIGINS works both as '["https://a.example.com"]'
        # and as "https://a.example.com, https://b.example.com".
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith("["):
                return json.loads(text)
            return [part.strip() for part in text.split(",") if part.strip()]
        return value

    @model_validator(mode="after")
    def _validate_production_settings(self) -> "Settings":
        if self.environment.strip().lower() not in {
            "production",
            "prod",
            "staging",
        }:
            return self
        if "*" in self.cors_allowed_origins:
            raise ValueError("wildcard CORS origins are not allowed in production")
        if len(self.jwt_secret_key) < 32:
            raise ValueError(
                "jwt_secret_key must be at least 32 characters in production"
            )
        if not self.database_url.startswith("postgresql"):
            raise ValueError("production database_url must use PostgreSQL (Neon)")
        redis_host = (urlparse(self.redis_url).hostname or "").lower()
        if redis_host in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError(
                "production redis_url must point at managed Redis, not localhost"
            )
        return self


settings = Settings()
