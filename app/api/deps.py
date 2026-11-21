from datetime import UTC, datetime, timedelta

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.database import get_db
from app.core.security import decode_token, hash_token
from app.models.enums import UserRole
from app.models.user import User, UserSession

bearer_scheme = HTTPBearer(auto_error=False)

ALL_STAFF_ROLES = (
    UserRole.ADMIN,
    UserRole.TRESORIER,
    UserRole.MEMBRE,
    UserRole.ENCADRANT,
)
CONTENT_EDITOR_ROLES = (UserRole.ADMIN, UserRole.TRESORIER, UserRole.MEMBRE)
PLANNING_EDITOR_ROLES = (UserRole.ADMIN, UserRole.TRESORIER, UserRole.MEMBRE)
REGISTRY_ENTRY_ROLES = (UserRole.ADMIN, UserRole.TRESORIER, UserRole.MEMBRE)
TREASURY_ROLES = (UserRole.ADMIN, UserRole.TRESORIER)
AUDIT_READ_ROLES = (UserRole.ADMIN, UserRole.ENCADRANT)


async def get_current_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    """
    Résout l'utilisateur connecté via :
    1) En-tête `Authorization: Bearer <access_token>` (lié à une session active en base)
    2) Cookie httpOnly `cb3_session` vérifié côté base (empreinte SHA-256).
    Bloque toute route autre que /auth/change-password, /auth/logout et /auth/me
    lorsque `must_change_password` est actif.
    """
    settings = get_settings()
    now = datetime.now(UTC)
    sess: UserSession | None = None

    if credentials and credentials.credentials:
        try:
            payload = decode_token(credentials.credentials)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Jeton d'authentification invalide ou expiré.",
            ) from exc
        if payload.get("type") != "access" or not payload.get("sub") or not payload.get("sid"):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Jeton d'accès invalide.",
            )
        res = await db.execute(
            select(UserSession).where(
                UserSession.id == payload["sid"],
                UserSession.user_id == payload["sub"],
            )
        )
        sess = res.scalar_one_or_none()
    else:
        raw_cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
        if not raw_cookie:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Authentification requise.",
            )
        t_hash = hash_token(raw_cookie)
        res = await db.execute(select(UserSession).where(UserSession.token_hash == t_hash))
        sess = res.scalar_one_or_none()

    if sess is None or sess.is_revoked:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session invalide, révoquée ou expirée.",
        )

    exp = sess.expires_at.replace(tzinfo=UTC) if sess.expires_at.tzinfo is None else sess.expires_at
    if exp <= now:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session expirée.",
        )

    # Renouvellement glissant de la session active
    sess.last_seen_at = now
    sess.expires_at = now + timedelta(hours=settings.SESSION_IDLE_HOURS)
    await db.commit()

    request.state.session_id = sess.id

    user_res = await db.execute(select(User).where(User.id == sess.user_id))
    user = user_res.scalar_one_or_none()
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable ou désactivé.",
        )

    prefix = settings.API_V1_PREFIX.rstrip("/")
    allowed_when_pwd_change_required = {
        f"{prefix}/auth/change-password",
        f"{prefix}/auth/logout",
        f"{prefix}/auth/me",
    }
    if user.must_change_password and request.url.path not in allowed_when_pwd_change_required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Changement de mot de passe obligatoire avant d'accéder à cette ressource.",
        )

    return user


async def get_optional_user(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: AsyncSession = Depends(get_db),
) -> User | None:
    try:
        return await get_current_user(request=request, credentials=credentials, db=db)
    except HTTPException as exc:
        if exc.status_code == status.HTTP_403_FORBIDDEN:
            raise
        return None


def require_roles(*allowed_roles: UserRole):
    """Dépendance RBAC vérifiant le rôle côté serveur."""

    async def _role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Droits insuffisants pour effectuer cette action.",
            )
        return current_user

    return _role_checker


async def require_admin(
    current_user: User = Depends(require_roles(UserRole.ADMIN)),
) -> User:
    return current_user
