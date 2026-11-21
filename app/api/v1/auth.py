from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import get_current_user, get_optional_user
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import client_ip, rate_limiter
from app.core.security import (
    generate_totp_secret,
    get_totp_uri,
    hash_password,
    validate_password_strength,
    verify_password,
    verify_totp_code,
)
from app.models.user import User
from app.schemas.auth import (
    ChangePasswordRequest,
    LoginRequest,
    RefreshRequest,
    TokenResponse,
    TotpDisableRequest,
    TotpSetupRequest,
    TotpSetupResponse,
    TotpVerifyRequest,
    UserOut,
)
from app.schemas.common import MessageResponse
from app.services.audit import record_audit_log
from app.services.auth_service import authenticate_user, refresh_user_tokens, revoke_session

router = APIRouter(prefix="/auth", tags=["Authentification"])


@router.post("/login", response_model=TokenResponse, summary="Connexion espace COVIDEBA")
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    settings = get_settings()
    ip = client_ip(request)
    await rate_limiter.check(
        f"login:ip:{ip}",
        max_requests=settings.LOGIN_MAX_ATTEMPTS * 3,
        window_seconds=settings.LOGIN_WINDOW_SECONDS,
    )
    await rate_limiter.check(
        f"login:ip_email:{ip}:{payload.email.lower()}",
        max_requests=settings.LOGIN_MAX_ATTEMPTS,
        window_seconds=settings.LOGIN_WINDOW_SECONDS,
    )

    user, access_token, refresh_token, raw_session_token = await authenticate_user(
        db,
        email=payload.email,
        password=payload.password,
        totp_code=payload.totp_code,
        ip_address=ip,
        user_agent=request.headers.get("user-agent"),
        remember_device=payload.remember_device,
    )

    max_age = settings.SESSION_IDLE_HOURS * 3600 * (30 if payload.remember_device else 1)
    response.set_cookie(
        key=settings.SESSION_COOKIE_NAME,
        value=raw_session_token,
        httponly=True,
        secure=settings.SESSION_COOKIE_SECURE,
        samesite=settings.SESSION_COOKIE_SAMESITE,
        max_age=max_age,
        path="/",
    )

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/refresh", response_model=TokenResponse, summary="Rafraîchir le jeton d'accès")
async def refresh_tokens(
    payload: RefreshRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    settings = get_settings()
    user, access_token, new_refresh = await refresh_user_tokens(
        db, refresh_token=payload.refresh_token, ip_address=client_ip(request)
    )
    return TokenResponse(
        access_token=access_token,
        refresh_token=new_refresh,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        user=UserOut.model_validate(user),
    )


@router.post("/logout", response_model=MessageResponse, summary="Déconnexion")
async def logout(
    request: Request,
    response: Response,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    settings = get_settings()
    raw_cookie = request.cookies.get(settings.SESSION_COOKIE_NAME)
    session_id = getattr(request.state, "session_id", None) if current_user else None
    if session_id:
        await revoke_session(db, session_id=session_id)
    if raw_cookie:
        await revoke_session(db, raw_session_token=raw_cookie)
    response.delete_cookie(key=settings.SESSION_COOKIE_NAME, path="/")
    return MessageResponse(message="Déconnexion effectuée.")


@router.get("/me", response_model=UserOut, summary="Profil de l'utilisateur connecté")
async def get_me(current_user: User = Depends(get_current_user)) -> UserOut:
    return UserOut.model_validate(current_user)


@router.post("/change-password", response_model=MessageResponse, summary="Changer son mot de passe")
async def change_password(
    payload: ChangePasswordRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    settings = get_settings()
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le mot de passe actuel est incorrect.",
        )
    errors = validate_password_strength(payload.new_password, settings.PASSWORD_MIN_LENGTH)
    if errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(errors))

    current_user.password_hash = hash_password(payload.new_password)
    current_user.must_change_password = False
    await revoke_session(db, user_id=current_user.id)
    await record_audit_log(
        db,
        action="auth.password_changed",
        resource_type="user",
        resource_id=current_user.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message="Mot de passe mis à jour. Veuillez vous reconnecter.")


@router.post("/2fa/setup", response_model=TotpSetupResponse, summary="Initialiser la double authentification TOTP")
async def setup_totp(
    payload: TotpSetupRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> TotpSetupResponse:
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La double authentification est déjà activée. Désactivez-la d'abord avec votre mot de passe et un code TOTP.",
        )
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Mot de passe actuel incorrect.",
        )
    secret = generate_totp_secret()
    current_user.totp_secret = secret
    current_user.totp_enabled = False
    await db.commit()
    return TotpSetupResponse(
        secret=secret,
        provisioning_uri=get_totp_uri(secret, current_user.email),
    )


@router.post("/2fa/enable", response_model=MessageResponse, summary="Activer la double authentification TOTP")
async def enable_totp(
    payload: TotpVerifyRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if current_user.totp_enabled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="La double authentification est déjà activée.",
        )
    if not current_user.totp_secret or not verify_totp_code(current_user.totp_secret, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code TOTP invalide.")
    current_user.totp_enabled = True
    await record_audit_log(
        db,
        action="auth.totp_enabled",
        resource_type="user",
        resource_id=current_user.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message="Double authentification activée.")


@router.post("/2fa/disable", response_model=MessageResponse, summary="Désactiver la double authentification TOTP")
async def disable_totp(
    payload: TotpDisableRequest,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    if not current_user.totp_enabled or not current_user.totp_secret:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="2FA non activée.")
    if not verify_password(payload.current_password, current_user.password_hash):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Mot de passe actuel incorrect.")
    if not verify_totp_code(current_user.totp_secret, payload.code):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Code TOTP invalide.")
    current_user.totp_enabled = False
    current_user.totp_secret = None
    await record_audit_log(
        db,
        action="auth.totp_disabled",
        resource_type="user",
        resource_id=current_user.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message="Double authentification désactivée.")
