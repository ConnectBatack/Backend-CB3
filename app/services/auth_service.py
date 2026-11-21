from datetime import UTC, datetime, timedelta

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.rate_limit import rate_limiter
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    generate_opaque_token,
    hash_password,
    hash_token,
    needs_rehash,
    verify_password,
    verify_totp_code,
)
from app.models.user import User, UserSession
from app.services.audit import record_audit_log


async def authenticate_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    totp_code: str | None = None,
    ip_address: str | None = None,
    user_agent: str | None = None,
    remember_device: bool = False,
) -> tuple[User, str, str, str]:
    """
    Authentifie un utilisateur et crée une session serveur + JWT liés à cette session.
    Protection brute-force par IP (délai progressif et rate limit) sans verrouiller le compte.
    """
    settings = get_settings()
    normalized_email = email.strip().lower()
    now = datetime.now(UTC)

    res = await db.execute(select(User).where(User.email == normalized_email))
    user = res.scalar_one_or_none()

    invalid_exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Identifiants invalides.",
    )

    if user is None:
        await record_audit_log(
            db,
            action="auth.login_failed",
            resource_type="user",
            actor_email=normalized_email,
            ip_address=ip_address,
            details={"reason": "unknown_email"},
        )
        await db.commit()
        await rate_limiter.record_login_failure_backoff(ip_address or "unknown")
        raise invalid_exc

    if not user.is_active:
        await record_audit_log(
            db,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.id,
            actor=user,
            ip_address=ip_address,
            details={"reason": "inactive"},
        )
        await db.commit()
        await rate_limiter.record_login_failure_backoff(ip_address or "unknown")
        raise invalid_exc

    if not verify_password(password, user.password_hash):
        user.failed_login_attempts += 1
        await record_audit_log(
            db,
            action="auth.login_failed",
            resource_type="user",
            resource_id=user.id,
            actor=user,
            ip_address=ip_address,
            details={"attempts": user.failed_login_attempts},
        )
        await db.commit()
        await rate_limiter.record_login_failure_backoff(ip_address or "unknown")
        raise invalid_exc

    if user.totp_enabled:
        if not totp_code or not user.totp_secret or not verify_totp_code(user.totp_secret, totp_code):
            await record_audit_log(
                db,
                action="auth.login_failed_totp",
                resource_type="user",
                resource_id=user.id,
                actor=user,
                ip_address=ip_address,
            )
            await db.commit()
            await rate_limiter.record_login_failure_backoff(ip_address or "unknown")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Code de double authentification (2FA) requis ou invalide.",
            )

    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)

    user.failed_login_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    rate_limiter.reset_ip_failures(ip_address or "unknown")

    raw_session_token = generate_opaque_token(32)
    idle_hours = settings.SESSION_IDLE_HOURS * (30 if remember_device else 1)

    session = UserSession(
        user_id=user.id,
        token_hash=hash_token(raw_session_token),
        refresh_token_hash=None,
        user_agent=(user_agent or "")[:512] or None,
        ip_address=ip_address,
        expires_at=now + timedelta(hours=idle_hours),
        last_seen_at=now,
    )
    db.add(session)
    await db.flush()

    refresh_token = create_refresh_token(user.id, session.id)
    access_token = create_access_token(user.id, user.role.value, session.id)
    session.refresh_token_hash = hash_token(refresh_token)

    await record_audit_log(
        db,
        action="auth.login_success",
        resource_type="user",
        resource_id=user.id,
        actor=user,
        ip_address=ip_address,
    )
    await db.commit()
    await db.refresh(user)
    return user, access_token, refresh_token, raw_session_token


async def refresh_user_tokens(
    db: AsyncSession, *, refresh_token: str, ip_address: str | None = None
) -> tuple[User, str, str]:
    """Renouvelle la paire access/refresh token par rotation sécurisée."""
    try:
        payload = decode_token(refresh_token)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Jeton de rafraîchissement invalide ou expiré.",
        ) from exc

    if payload.get("type") != "refresh" or not payload.get("sub") or not payload.get("sid"):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Type de jeton invalide.",
        )

    token_h = hash_token(refresh_token)
    res = await db.execute(
        select(UserSession).where(
            UserSession.id == payload["sid"],
            UserSession.refresh_token_hash == token_h,
        )
    )
    sess = res.scalar_one_or_none()
    now = datetime.now(UTC)
    if sess is None or sess.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session révoquée ou introuvable.",
        )

    exp = sess.expires_at.replace(tzinfo=UTC) if sess.expires_at.tzinfo is None else sess.expires_at
    if exp <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expirée.",
        )

    user_res = await db.execute(select(User).where(User.id == sess.user_id))
    user = user_res.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Compte inactif.",
        )

    new_refresh = create_refresh_token(user.id, sess.id)
    new_access = create_access_token(user.id, user.role.value, sess.id)
    sess.refresh_token_hash = hash_token(new_refresh)
    sess.last_seen_at = now
    if ip_address:
        sess.ip_address = ip_address
    await db.commit()
    return user, new_access, new_refresh


async def revoke_session(
    db: AsyncSession,
    *,
    raw_session_token: str | None = None,
    session_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Révoque une session par cookie, par session_id, ou toutes les sessions d'un utilisateur."""
    if session_id:
        res = await db.execute(select(UserSession).where(UserSession.id == session_id))
        sess = res.scalar_one_or_none()
        if sess is not None:
            sess.is_revoked = True
            await db.commit()
    elif raw_session_token:
        t_hash = hash_token(raw_session_token)
        res = await db.execute(select(UserSession).where(UserSession.token_hash == t_hash))
        sess = res.scalar_one_or_none()
        if sess is not None:
            sess.is_revoked = True
            await db.commit()
    elif user_id:
        res = await db.execute(
            select(UserSession).where(UserSession.user_id == user_id, UserSession.is_revoked.is_(False))
        )
        for sess in res.scalars().all():
            sess.is_revoked = True
        await db.commit()
