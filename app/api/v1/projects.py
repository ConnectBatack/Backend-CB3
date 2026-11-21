from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CONTENT_EDITOR_ROLES, get_optional_user, require_admin, require_roles
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.content import Project
from app.models.enums import ProjectStatus
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.content import ProjectCreate, ProjectOut, ProjectUpdate
from app.services.audit import record_audit_log
from app.services.ledger_service import compute_project_ledger_stats

router = APIRouter(prefix="/projects", tags=["Projets"])


def to_project_out(
    p: Project,
    *,
    collected_eur: Decimal = Decimal("0.00"),
    contributors_count: int = 0,
) -> ProjectOut:
    settings = get_settings()
    prefix = settings.API_V1_PREFIX.rstrip("/")
    photo_url = None
    if p.photo_id:
        photo_url = f"{prefix}/media/files/{p.photo_id}"
    elif p.photo_slug:
        photo_url = f"{prefix}/media/files-by-slug/{p.photo_slug}"

    target = float(p.target_amount_eur or 0)
    collected = float(collected_eur)
    progress = min(100, round((collected / target) * 100)) if target > 0 else 0

    return ProjectOut(
        id=p.id,
        slug=p.slug,
        category=p.category or {},
        title=p.title or {},
        tagline=p.tagline or {},
        description=p.description or {},
        photo_id=p.photo_id,
        photo_slug=p.photo_slug,
        photo_url=photo_url,
        target_amount_eur=p.target_amount_eur,
        collected_amount_eur=collected_eur.quantize(Decimal("0.01")),
        contributors_count=contributors_count,
        progress_percent=progress,
        is_featured_need=p.is_featured_need,
        display_order=p.display_order,
        status=p.status,
        created_at=p.created_at,
        updated_at=p.updated_at,
    )


@router.get("", response_model=list[ProjectOut], summary="Lister les projets du village")
async def list_projects(
    include_archived: bool = False,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[ProjectOut]:
    query = select(Project)
    if not (include_archived and current_user is not None):
        query = query.where(Project.status != ProjectStatus.ARCHIVED)
    res = await db.execute(query.order_by(Project.display_order.asc(), Project.created_at.asc()))
    stats = await compute_project_ledger_stats(db)
    return [
        to_project_out(
            p,
            collected_eur=stats.get(p.id, (Decimal("0.00"), 0))[0],
            contributors_count=stats.get(p.id, (Decimal("0.00"), 0))[1],
        )
        for p in res.scalars().all()
    ]


@router.get("/{slug}", response_model=ProjectOut, summary="Détail d'un projet")
async def get_project(
    slug: str,
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    res = await db.execute(select(Project).where(Project.slug == slug))
    proj = res.scalar_one_or_none()
    if proj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")
    stats = await compute_project_ledger_stats(db)
    collected_eur, contributors = stats.get(proj.id, (Decimal("0.00"), 0))
    return to_project_out(proj, collected_eur=collected_eur, contributors_count=contributors)


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, summary="Créer un projet")
async def create_project(
    payload: ProjectCreate,
    request: Request,
    current_user: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    existing = await db.execute(select(Project).where(Project.slug == payload.slug))
    if existing.scalar_one_or_none() is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un projet avec ce slug existe déjà.")

    if payload.photo_id:
        media_res = await db.execute(select(MediaAsset).where(MediaAsset.id == payload.photo_id))
        if media_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média photo_id introuvable.")

    proj = Project(
        **payload.model_dump(),
        collected_amount_eur=Decimal("0.00"),
        contributors_count=0,
        created_by_id=current_user.id,
    )
    db.add(proj)
    await db.flush()
    await record_audit_log(
        db,
        action="projects.created",
        resource_type="project",
        resource_id=proj.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"slug": proj.slug},
    )
    await db.commit()
    await db.refresh(proj)
    return to_project_out(proj)


@router.patch("/{slug}", response_model=ProjectOut, summary="Mettre à jour un projet")
async def update_project(
    slug: str,
    payload: ProjectUpdate,
    request: Request,
    current_user: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    res = await db.execute(select(Project).where(Project.slug == slug))
    proj = res.scalar_one_or_none()
    if proj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")

    updates = payload.model_dump(exclude_unset=True)
    if updates.get("photo_id"):
        media_res = await db.execute(select(MediaAsset).where(MediaAsset.id == updates["photo_id"]))
        if media_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média photo_id introuvable.")

    for k, v in updates.items():
        setattr(proj, k, v)

    await record_audit_log(
        db,
        action="projects.updated",
        resource_type="project",
        resource_id=proj.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"fields": list(updates.keys())},
    )
    await db.commit()
    await db.refresh(proj)
    stats = await compute_project_ledger_stats(db)
    collected_eur, contributors = stats.get(proj.id, (Decimal("0.00"), 0))
    return to_project_out(proj, collected_eur=collected_eur, contributors_count=contributors)


@router.delete("/{slug}", response_model=MessageResponse, summary="Supprimer un projet (admin)")
async def delete_project(
    slug: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    res = await db.execute(select(Project).where(Project.slug == slug))
    proj = res.scalar_one_or_none()
    if proj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")

    proj_id = proj.id
    await db.delete(proj)
    await record_audit_log(
        db,
        action="projects.deleted",
        resource_type="project",
        resource_id=proj_id,
        actor=admin,
        ip_address=client_ip(request),
        details={"slug": slug},
    )
    await db.commit()
    return MessageResponse(message="Projet supprimé.")
