from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.core.security import hash_password, validate_password_strength
from app.models.enums import UserRole
from app.models.user import User
from app.schemas.auth import AdminResetPasswordRequest, UserCreate, UserOut, UserUpdate
from app.schemas.common import MessageResponse, PaginatedResponse
from app.services.audit import record_audit_log
from app.services.auth_service import revoke_session

router = APIRouter(prefix="/users", tags=["Administration - Utilisateurs & Rôles"])


@router.get("", response_model=PaginatedResponse[UserOut], summary="Lister les comptes (admin)")
async def list_users(
    role: UserRole | None = None,
    is_active: bool | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[UserOut]:
    query = select(User)
    count_query = select(func.count(User.id))
    if role is not None:
        query = query.where(User.role == role)
        count_query = count_query.where(User.role == role)
    if is_active is not None:
        query = query.where(User.is_active == is_active)
        count_query = count_query.where(User.is_active == is_active)

    total = (await db.execute(count_query)).scalar_one()
    res = await db.execute(query.order_by(User.created_at.desc()).limit(limit).offset(offset))
    items = [UserOut.model_validate(u) for u in res.scalars().all()]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED, summary="Créer un compte (admin)")
async def create_user(
    payload: UserCreate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    settings = get_settings()
    email = payload.email.strip().lower()
    existing = await db.execute(select(User).where(User.email == email))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un compte existe déjà avec cet e-mail.")

    pwd_errors = validate_password_strength(payload.password, settings.PASSWORD_MIN_LENGTH)
    if pwd_errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(pwd_errors))

    user = User(
        email=email,
        full_name=payload.full_name.strip(),
        role=payload.role,
        password_hash=hash_password(payload.password),
        must_change_password=payload.must_change_password,
        is_active=True,
    )
    db.add(user)
    await db.flush()
    await record_audit_log(
        db,
        action="users.created",
        resource_type="user",
        resource_id=user.id,
        actor=admin,
        ip_address=client_ip(request),
        details={"email": email, "role": payload.role.value},
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.get("/{user_id}", response_model=UserOut, summary="Détail d'un utilisateur (admin)")
async def get_user(
    user_id: str,
    _: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")
    return UserOut.model_validate(user)


@router.patch("/{user_id}", response_model=UserOut, summary="Modifier rôle ou statut (admin)")
async def update_user(
    user_id: str,
    payload: UserUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> UserOut:
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")

    if user.id == admin.id and payload.is_active is False:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Impossible de désactiver votre propre compte.")
    if user.id == admin.id and payload.role is not None and payload.role != UserRole.ADMIN:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Impossible de rétrograder votre propre rôle.")

    changes = {}
    if payload.full_name is not None:
        user.full_name = payload.full_name.strip()
        changes["full_name"] = user.full_name
    if payload.role is not None:
        user.role = payload.role
        changes["role"] = payload.role.value
    if payload.is_active is not None:
        user.is_active = payload.is_active
        changes["is_active"] = payload.is_active
        if not payload.is_active:
            await revoke_session(db, user_id=user.id)
    if payload.must_change_password is not None:
        user.must_change_password = payload.must_change_password
        changes["must_change_password"] = payload.must_change_password

    await record_audit_log(
        db,
        action="users.updated",
        resource_type="user",
        resource_id=user.id,
        actor=admin,
        ip_address=client_ip(request),
        details=changes,
    )
    await db.commit()
    await db.refresh(user)
    return UserOut.model_validate(user)


@router.post("/{user_id}/reset-password", response_model=MessageResponse, summary="Réinitialiser un mot de passe (admin)")
async def admin_reset_password(
    user_id: str,
    payload: AdminResetPasswordRequest,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    settings = get_settings()
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")

    pwd_errors = validate_password_strength(payload.new_password, settings.PASSWORD_MIN_LENGTH)
    if pwd_errors:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=" ".join(pwd_errors))

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = payload.must_change_password
    user.failed_login_attempts = 0
    user.locked_until = None
    await revoke_session(db, user_id=user.id)
    await record_audit_log(
        db,
        action="users.password_reset",
        resource_type="user",
        resource_id=user.id,
        actor=admin,
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message="Mot de passe réinitialisé et sessions révoquées.")


@router.post("/{user_id}/unlock", response_model=MessageResponse, summary="Déverrouiller un compte (admin)")
async def unlock_user(
    user_id: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    res = await db.execute(select(User).where(User.id == user_id))
    user = res.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Utilisateur introuvable.")
    user.failed_login_attempts = 0
    user.locked_until = None
    await record_audit_log(
        db,
        action="users.unlocked",
        resource_type="user",
        resource_id=user.id,
        actor=admin,
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message="Compte déverrouillé.")

