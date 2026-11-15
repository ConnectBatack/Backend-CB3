from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PARITE_FCFA = Decimal("655.957")
SUPPORTED_LANGUAGES = ("fr", "en", "es", "nl")


class Settings(BaseSettings):
    """Configuration centralisée chargée depuis .env et les variables d'environnement."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "ConnectBatack 3 API"
    APP_ENV: Literal["development", "staging", "production", "test"] = "development"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    PUBLIC_SITE_URL: str = "http://localhost:3000"
    BACKEND_PUBLIC_URL: str = "http://localhost:8000"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./batack_dev.db"
    TEST_DATABASE_URL: str = "sqlite+aiosqlite:///./test_batack.db"

    # Redis (optional)
    REDIS_URL: str = ""

    # Security / Auth
    SECRET_KEY: str = "dev_insecure_change_me_use_at_least_32_characters_random_secret"
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SESSION_COOKIE_NAME: str = "cb3_session"
    SESSION_IDLE_HOURS: int = 8
    SESSION_COOKIE_SECURE: bool = False
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_SECONDS: int = 900
    PASSWORD_MIN_LENGTH: int = 12

    # Local Storage
    STORAGE_DIR: str = "./storage"
    MAX_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MiB

    # Stripe / Donations
    STRIPE_SECRET_KEY: str = ""
    STRIPE_WEBHOOK_SECRET: str = ""
    PAIEMENT_REEL_AUTORISE: str = "non"
    DON_MENSUEL_ACTIF: bool = False
    DON_MONTANT_MIN_EUR: int = 5
    DON_MONTANT_MAX_EUR: int = 5000

    # Seed
    SEED_ON_STARTUP: bool = False
    ADMIN_DEFAULT_EMAIL: str = "admin@batack.org"
    ADMIN_DEFAULT_PASSWORD: str = "BatackAdmin2026!"
    ADMIN_DEFAULT_NAME: str = "Administrateur ENSPY"

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if len(v) < 32:
            raise ValueError("SECRET_KEY doit contenir au moins 32 caractères.")
        return v

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def storage_path(self) -> Path:
        return Path(self.STORAGE_DIR).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()

