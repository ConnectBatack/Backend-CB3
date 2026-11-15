from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, require_roles
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import client_ip, rate_limiter
from app.core.security import generate_opaque_token, hash_token
from app.models.content import Project, ProjectSubscription
from app.models.enums import SubscriptionStatus
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.content import SubscriptionActionResponse, SubscriptionCreate, SubscriptionOut

router = APIRouter(prefix="/subscriptions", tags=["Suivi des projets (RGPD)"])


@router.post("/follow", response_model=SubscriptionActionResponse, status_code=status.HTTP_201_CREATED, summary="S'inscrire au suivi d'un projet (double opt-in)")
async def follow_project(
    payload: SubscriptionCreate,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> SubscriptionActionResponse:
    await rate_limiter.check(f"suivi:{client_ip(request)}", max_requests=10, window_seconds=300)

    proj_res = await db.execute(select(Project).where(Project.slug == payload.projet))
    project = proj_res.scalar_one_or_none()
    if project is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Projet inconnu.")

    email = payload.email.strip().lower()
    existing_res = await db.execute(
        select(ProjectSubscription).where(
            ProjectSubscription.email == email,
            ProjectSubscription.project_id == project.id,
        )
    )
    sub = existing_res.scalar_one_or_none()
    now = datetime.now(UTC)
    confirm_raw = generate_opaque_token(24)
    unsub_raw = generate_opaque_token(24)

    if sub is not None and sub.status == SubscriptionStatus.ACTIVE:
        return SubscriptionActionResponse(
            message="Vous suivez déjà ce projet.",
            status=sub.status,
        )

    if sub is None:
        sub = ProjectSubscription(
            email=email,
            project_id=project.id,
            language=payload.langue,
            consent_given=True,
            consent_date=now,
            status=SubscriptionStatus.PENDING,
            confirm_token_hash=hash_token(confirm_raw),
            unsubscribe_token_hash=hash_token(unsub_raw),
        )
        db.add(sub)
    else:
        sub.language = payload.langue
        sub.consent_given = True
        sub.consent_date = now
        sub.status = SubscriptionStatus.PENDING
        sub.confirm_token_hash = hash_token(confirm_raw)
        sub.unsubscribe_token_hash = hash_token(unsub_raw)
        sub.unsubscribed_at = None

    await db.commit()
    settings = get_settings()
    # En développement/test, on expose les jetons pour permettre la validation sans serveur SMTP
    expose_tokens = settings.APP_ENV in ("development", "test")
    return SubscriptionActionResponse(
        message="Inscription enregistrée. Confirmez votre adresse e-mail.",
        status=SubscriptionStatus.PENDING,
        confirm_token=confirm_raw if expose_tokens else None,
        unsubscribe_token=unsub_raw if expose_tokens else None,
    )


@router.get("/confirm", response_model=SubscriptionActionResponse, summary="Confirmer l'inscription (double opt-in)")
async def confirm_subscription(
    token: str = Query(min_length=10),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionActionResponse:
    t_hash = hash_token(token)
    res = await db.execute(
        select(ProjectSubscription).where(ProjectSubscription.confirm_token_hash == t_hash)
    )
    sub = res.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lien de confirmation invalide ou expiré.")

    sub.status = SubscriptionStatus.ACTIVE
    sub.confirmed_at = datetime.now(UTC)
    sub.confirm_token_hash = None
    await db.commit()
    return SubscriptionActionResponse(message="Inscription confirmée.", status=sub.status)


@router.get("/unsubscribe", response_model=SubscriptionActionResponse, summary="Se désinscrire d'un projet")
async def unsubscribe_project(
    token: str = Query(min_length=10),
    db: AsyncSession = Depends(get_db),
) -> SubscriptionActionResponse:
    t_hash = hash_token(token)
    res = await db.execute(
        select(ProjectSubscription).where(ProjectSubscription.unsubscribe_token_hash == t_hash)
    )
    sub = res.scalar_one_or_none()
    if sub is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Lien de désinscription invalide.")

    sub.status = SubscriptionStatus.UNSUBSCRIBED
    sub.unsubscribed_at = datetime.now(UTC)
    sub.confirm_token_hash = None
    await db.commit()
    return SubscriptionActionResponse(message="Désinscription effectuée.", status=sub.status)


@router.get("", response_model=PaginatedResponse[SubscriptionOut], summary="Lister les abonnés (espace gestion)")
async def list_subscriptions(
    project_id: str | None = None,
    status_filter: SubscriptionStatus | None = Query(default=None, alias="status"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[SubscriptionOut]:
    query = select(ProjectSubscription)
    count_query = select(func.count(ProjectSubscription.id))
    if project_id:
        query = query.where(ProjectSubscription.project_id == project_id)
        count_query = count_query.where(ProjectSubscription.project_id == project_id)
    if status_filter is not None:
        query = query.where(ProjectSubscription.status == status_filter)
        count_query = count_query.where(ProjectSubscription.status == status_filter)

    total = (await db.execute(count_query)).scalar_one()
    res = await db.execute(query.order_by(ProjectSubscription.created_at.desc()).limit(limit).offset(offset))
    items = [SubscriptionOut.model_validate(s) for s in res.scalars().all()]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)

