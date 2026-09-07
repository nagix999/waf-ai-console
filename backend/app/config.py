from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="WAF_", env_file=".env", extra="ignore")

    app_name: str = "WAF AI Analysis Console"
    environment: str = "development"
    database_url: str = "sqlite+pysqlite:///./waf.db"

    admin_username: str = "admin"
    admin_password: str = "change-me-now"
    session_secret: str = "local-session-secret-change-before-deploy"
    session_https_only: bool = False
    session_max_age_seconds: int = 8 * 60 * 60

    data_encryption_key: str = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="
    encryption_key_version: str = "dev-v1"

    agent_mode: str = Field(default="stub", pattern="^(stub|moduagent)$")
    worker_role: str = Field(default="both", pattern="^(analysis|model_test|both)$")
    worker_poll_seconds: float = Field(default=1.0, ge=0.1, le=60)
    job_lease_seconds: int = Field(default=300, ge=30, le=3600)
    upload_max_bytes: int = Field(default=10 * 1024 * 1024, ge=1024)
    payload_max_bytes: int = Field(default=2 * 1024 * 1024, ge=1)
    # Deprecated compatibility setting. Egress authorization is DB-only.
    vllm_allowed_targets: str = ""
    vllm_test_lease_seconds: int = Field(default=900, ge=60, le=3600)
    verifier_confidence_threshold: float = Field(default=0.75, ge=0, le=1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
