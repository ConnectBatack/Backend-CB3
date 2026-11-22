from decimal import Decimal
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PARITE_FCFA = Decimal("655.957")
SUPPORTED_LANGUAGES = ("fr", "en", "es", "nl")

# Empreintes / valeurs historiquement publiées ou génériques à refuser absolument hors tests
FORBIDDEN_SECRETS = {
    "dev_insecure_change_me_use_at_least_32_characters_random_secret",
    "batack_dev_secret_key_change_in_production_min_48_chars_2026",
    "change-this-secret-key",
}
FORBIDDEN_PASSWORDS = {
    "BatackAdmin2026!",
    "BatackTresorier2026!",
    "BatackMembre2026!",
    "BatackEncadrant2026!",
    "batack_dev_password_change_me",
}


class Settings(BaseSettings):
    """Configuration centralisée chargée depuis .env et les variables d'environnement."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Application
    APP_NAME: str = "Batack API"
    TOTP_ISSUER_NAME: str = "Batack"
    APP_ENV: Literal["development", "staging", "production", "test"] = "production"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"
    PUBLIC_SITE_URL: str = "http://localhost:3000"
    BACKEND_PUBLIC_URL: str = "http://localhost:8001"
    CORS_ORIGINS: str = "http://localhost:3000"

    # Proxy & Rate limiting
    TRUST_PROXY_HEADERS: bool = False
    TRUSTED_PROXIES: str = "127.0.0.1,::1"
    LOGIN_MAX_ATTEMPTS: int = 5
    LOGIN_WINDOW_SECONDS: int = 900

    # Database
    DATABASE_URL: str = "sqlite+aiosqlite:///./batack_dev.db"
    TEST_DATABASE_URL: str = "sqlite+aiosqlite:///./test_batack.db"

    # Redis (optionnel)
    REDIS_URL: str = ""

    # Security / Auth
    SECRET_KEY: str = ""
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    SESSION_COOKIE_NAME: str = "cb3_session"
    SESSION_IDLE_HOURS: int = 8
    SESSION_COOKIE_SECURE: bool = True
    SESSION_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"
    PASSWORD_MIN_LENGTH: int = 12

    # Local Storage & Optional Frontend Seed Paths
    STORAGE_DIR: str = "./storage"
    MAX_UPLOAD_SIZE_BYTES: int = 10 * 1024 * 1024  # 10 MiB
    FRONTEND_IMAGES_DIR: str = ""
    FRONTEND_I18N_DIR: str = ""

    # SMTP (e-mails de confirmation double opt-in)
    SMTP_HOST: str = ""
    SMTP_PORT: int = 587
    SMTP_USER: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = "noreply@batack.org"
    SMTP_USE_TLS: bool = True

    # Seed
    SEED_ON_STARTUP: bool = False
    ADMIN_DEFAULT_EMAIL: str = "admin@batack.org"
    ADMIN_DEFAULT_PASSWORD: str = ""
    ADMIN_DEFAULT_NAME: str = "Administrateur ENSPY"

    # Traduction automatique (Google Translate API v2 si clé fournie, sinon endpoint public)
    GOOGLE_TRANSLATE_API_KEY: str = ""

    @field_validator("SECRET_KEY")
    @classmethod
    def validate_secret_key(cls, v: str) -> str:
        if not v or len(v) < 32:
            raise ValueError("SECRET_KEY doit être défini et contenir au moins 32 caractères.")
        if v.startswith("CHANGE_ME"):
            raise ValueError("SECRET_KEY doit être remplacé avant de démarrer l'application.")
        return v

    def validate_startup_security(self) -> None:
        """Vérifications strictes au démarrage (refus des valeurs publiées ou non sûres en production)."""
        if self.APP_ENV != "test" and self.SECRET_KEY in FORBIDDEN_SECRETS:
            raise RuntimeError("SECRET_KEY utilise une valeur déjà publiée : générez une nouvelle clé aléatoire.")
        if self.ADMIN_DEFAULT_PASSWORD and (
            self.ADMIN_DEFAULT_PASSWORD in FORBIDDEN_PASSWORDS
            or self.ADMIN_DEFAULT_PASSWORD.startswith("CHANGE_ME")
        ):
            raise RuntimeError("ADMIN_DEFAULT_PASSWORD utilise un mot de passe déjà publié ou générique.")
        if self.APP_ENV in ("production", "staging"):
            if self.DEBUG:
                raise RuntimeError("DEBUG=true est interdit en production/staging.")
            if not self.SESSION_COOKIE_SECURE:
                raise RuntimeError("SESSION_COOKIE_SECURE=true est obligatoire en production/staging.")
            if self.SEED_ON_STARTUP:
                raise RuntimeError("SEED_ON_STARTUP=true est interdit en production/staging.")

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.CORS_ORIGINS.split(",") if o.strip()]

    @property
    def trusted_proxies_set(self) -> set[str]:
        return {p.strip() for p in self.TRUSTED_PROXIES.split(",") if p.strip()}

    @property
    def storage_path(self) -> Path:
        return Path(self.STORAGE_DIR).resolve()


@lru_cache
def get_settings() -> Settings:
    return Settings()
