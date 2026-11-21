import hashlib
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
import pyotp
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError

from app.core.config import get_settings

# Paramètres OWASP recommandés pour Argon2id (m=19 MiB, t=2, p=1)
_password_hasher = PasswordHasher(
    time_cost=2,
    memory_cost=19456,
    parallelism=1,
    hash_len=32,
    salt_len=16,
)


def hash_password(password: str) -> str:
    """Hache un mot de passe avec Argon2id."""
    return _password_hasher.hash(password)


def verify_password(plain_password: str, password_hash: str) -> bool:
    """Vérifie un mot de passe contre son empreinte Argon2id."""
    try:
        return _password_hasher.verify(password_hash, plain_password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Indique si l'empreinte doit être recalculée avec les paramètres courants."""
    return _password_hasher.check_needs_rehash(password_hash)


def validate_password_strength(password: str, min_length: int = 12) -> list[str]:
    """Vérifie la robustesse minimale d'un mot de passe."""
    errors: list[str] = []
    if len(password) < min_length:
        errors.append(f"Le mot de passe doit contenir au moins {min_length} caractères.")
    if not any(c.islower() for c in password):
        errors.append("Le mot de passe doit contenir au moins une lettre minuscule.")
    if not any(c.isupper() for c in password):
        errors.append("Le mot de passe doit contenir au moins une lettre majuscule.")
    if not any(c.isdigit() for c in password):
        errors.append("Le mot de passe doit contenir au moins un chiffre.")
    return errors


def generate_opaque_token(nbytes: int = 32) -> str:
    """Génère un jeton aléatoire cryptographique (>= 256 bits par défaut)."""
    return secrets.token_urlsafe(nbytes)


def hash_token(token: str) -> str:
    """Calcule l'empreinte SHA-256 d'un jeton pour ne jamais stocker le jeton brut en base."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_access_token(
    subject: str,
    role: str,
    session_id: str,
    extra: dict[str, Any] | None = None,
) -> str:
    """Génère un JWT d'accès lié à une session serveur révocable."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "sid": session_id,
        "type": "access",
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
        "jti": secrets.token_hex(12),
    }
    if extra:
        payload.update(extra)
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(subject: str, session_id: str) -> str:
    """Génère un JWT de rafraîchissement lié à une session serveur."""
    settings = get_settings()
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "sub": subject,
        "sid": session_id,
        "type": "refresh",
        "iat": now,
        "exp": now + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
        "jti": secrets.token_hex(16),
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


def decode_token(token: str) -> dict[str, Any]:
    """Décode et valide un JWT."""
    settings = get_settings()
    return jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])


def generate_totp_secret() -> str:
    """Génère un secret TOTP Base32 pour la double authentification."""
    return pyotp.random_base32()


def get_totp_uri(secret: str, email: str, issuer: str | None = None) -> str:
    """Construit l'URI otpauth:// pour les applications TOTP."""
    issuer_name = issuer or get_settings().TOTP_ISSUER_NAME
    return pyotp.totp.TOTP(secret).provisioning_uri(name=email, issuer_name=issuer_name)


def verify_totp_code(secret: str, code: str) -> bool:
    """Vérifie un code TOTP à 6 chiffres (fenêtre +/- 1 pas de 30 s)."""
    cleaned = code.strip().replace(" ", "")
    if not cleaned.isdigit():
        return False
    return pyotp.TOTP(secret).verify(cleaned, valid_window=1)
