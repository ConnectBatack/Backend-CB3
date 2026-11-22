from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CONTENT_EDITOR_ROLES, get_optional_user, require_admin, require_roles
from app.core.config import SUPPORTED_LANGUAGES, get_settings
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.content import Project
from app.models.enums import ProjectStatus
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.content import (
    ProjectCreate,
    ProjectOut,
    ProjectTranslateRequest,
    ProjectTranslateResponse,
    ProjectUpdate,
)
from app.services.audit import record_audit_log
from app.services.ledger_service import compute_project_ledger_stats
from app.services.translate_service import complete_project_translations, slugify_title

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


async def _unique_slug(db: AsyncSession, raw_slug: str) -> str:
    base = slugify_title(raw_slug)
    candidate = base
    suffix = 2
    while (await db.execute(select(Project.id).where(Project.slug == candidate))).scalar_one_or_none() is not None:
        candidate = f"{base[:56]}-{suffix}"
        suffix += 1
    return candidate


def _infer_source_lang(payload_lang: str | None, *maps: dict[str, str] | None) -> str:
    if payload_lang in SUPPORTED_LANGUAGES:
        return payload_lang
    for m in maps:
        if m:
            for k, v in m.items():
                if k in SUPPORTED_LANGUAGES and (v or "").strip():
                    return k
    return "fr"


@router.post("/translate", response_model=ProjectTranslateResponse, summary="Traduire automatiquement un projet via Google Translate")
async def translate_project_texts(
    payload: ProjectTranslateRequest,
    _: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
) -> ProjectTranslateResponse:
    src = payload.source_language
    fields, all_ok = await complete_project_translations(
        source_lang=src,
        category={src: payload.category} if payload.category.strip() else {},
        title={src: payload.title} if payload.title.strip() else {},
        tagline={src: payload.tagline} if payload.tagline.strip() else {},
        description={src: payload.description} if payload.description.strip() else {},
        overwrite=True,
    )
    return ProjectTranslateResponse(
        category=fields["category"],
        title=fields["title"],
        tagline=fields["tagline"],
        description=fields["description"],
        translated_with_google=all_ok,
    )


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, summary="Créer un projet")
async def create_project(
    payload: ProjectCreate,
    request: Request,
    current_user: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> ProjectOut:
    if payload.slug:
        existing = await db.execute(select(Project).where(Project.slug == payload.slug))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Un projet avec ce slug existe déjà.")
        slug = payload.slug
    else:
        src = _infer_source_lang(payload.source_language, payload.title)
        title_text = (payload.title.get(src) or next(iter(payload.title.values()), "")).strip()
        slug = await _unique_slug(db, title_text or "projet")

    if payload.photo_id:
        media_res = await db.execute(select(MediaAsset).where(MediaAsset.id == payload.photo_id))
        if media_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média photo_id introuvable.")

    data = payload.model_dump(exclude={"slug", "source_language", "auto_translate", "overwrite_translations"})
    if payload.auto_translate:
        src = _infer_source_lang(payload.source_language, payload.title, payload.description, payload.tagline, payload.category)
        translated, _ = await complete_project_translations(
            source_lang=src,
            category=data["category"],
            title=data["title"],
            tagline=data["tagline"],
            description=data["description"],
            overwrite=payload.overwrite_translations,
        )
        data.update(translated)

    proj = Project(
        slug=slug,
        **data,
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

    updates = payload.model_dump(
        exclude_unset=True,
        exclude={"source_language", "auto_translate", "overwrite_translations"},
    )
    if updates.get("photo_id"):
        media_res = await db.execute(select(MediaAsset).where(MediaAsset.id == updates["photo_id"]))
        if media_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média photo_id introuvable.")

    if payload.auto_translate:
        src = _infer_source_lang(
            payload.source_language,
            updates.get("title"),
            updates.get("description"),
            updates.get("tagline"),
            updates.get("category"),
        )
        merged = {
            "category": {**(proj.category or {}), **(updates.get("category") or {})},
            "title": {**(proj.title or {}), **(updates.get("title") or {})},
            "tagline": {**(proj.tagline or {}), **(updates.get("tagline") or {})},
            "description": {**(proj.description or {}), **(updates.get("description") or {})},
        }
        translated, _ = await complete_project_translations(
            source_lang=src,
            category=merged["category"],
            title=merged["title"],
            tagline=merged["tagline"],
            description=merged["description"],
            overwrite=payload.overwrite_translations,
        )
        updates.update(translated)

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
