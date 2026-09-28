from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "ATS"
    environment: str = "development"
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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
    )


settings = Settings()
