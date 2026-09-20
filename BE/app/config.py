from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "HealthGuard API"
    environment: Literal["development", "test", "production"] = "development"
    database_url: str = "postgresql+asyncpg://eldercare_app:CHANGE_ME@localhost:5432/eldercare_dev"
    jwt_secret: str = "development-only-change-this-secret"
    jwt_algorithm: str = "HS256"
    access_token_minutes: int = Field(default=480, ge=5, le=10080)
    cookie_name: str = "healthguard_access_token"
    cookie_secure: bool = False
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    cors_origins: list[str] | str = ["http://localhost:3000"]
    frontend_base_url: str = "http://localhost:3000"
    default_timezone: str = "Asia/Ho_Chi_Minh"
    worker_poll_seconds: int = Field(default=30, ge=5, le=3600)
    occurrence_horizon_hours: int = Field(default=36, ge=1, le=168)
    telegram_bot_token: str | None = None
    telegram_bot_username: str | None = None
    telegram_webhook_secret: str | None = None

    @field_validator("cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: object) -> object:
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("jwt_secret")
    @classmethod
    def validate_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
