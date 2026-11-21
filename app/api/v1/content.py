from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CONTENT_EDITOR_ROLES, get_optional_user, require_admin, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.content import SiteContent
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.content import SiteContentOut, SiteContentUpsert
from app.services.audit import record_audit_log

router = APIRouter(prefix="/contents", tags=["Contenus éditoriaux (CMS)"])


@router.get("", response_model=list[SiteContentOut], summary="Lister les blocs de contenu")
async def list_contents(
    section: str | None = None,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> list[SiteContentOut]:
    query = select(SiteContent)
    if current_user is None:
        query = query.where(SiteContent.is_published.is_(True))
    if section:
        query = query.where(SiteContent.section == section)
    res = await db.execute(query.order_by(SiteContent.key.asc()))
    return [SiteContentOut.model_validate(item) for item in res.scalars().all()]


@router.get("/{key}", response_model=SiteContentOut, summary="Récupérer un bloc de contenu par clé")
async def get_content(
    key: str,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> SiteContentOut:
    res = await db.execute(select(SiteContent).where(SiteContent.key == key))
    item = res.scalar_one_or_none()
    if item is None or (not item.is_published and current_user is None):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contenu introuvable.")
    return SiteContentOut.model_validate(item)


@router.put("/{key}", response_model=SiteContentOut, summary="Créer ou modifier un bloc de contenu")
async def upsert_content(
    key: str,
    payload: SiteContentUpsert,
    request: Request,
    current_user: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> SiteContentOut:
    if payload.media_id:
        m_res = await db.execute(select(MediaAsset.id).where(MediaAsset.id == payload.media_id))
        if m_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média introuvable.")
    res = await db.execute(select(SiteContent).where(SiteContent.key == key))
    item = res.scalar_one_or_none()
    if item is None:
        item = SiteContent(
            key=key,
            section=payload.section,
            content=payload.content,
            media_id=payload.media_id,
            is_published=payload.is_published,
            updated_by_id=current_user.id,
        )
        db.add(item)
        action = "contents.created"
    else:
        item.section = payload.section
        item.content = payload.content
        item.media_id = payload.media_id
        item.is_published = payload.is_published
        item.updated_by_id = current_user.id
        action = "contents.updated"

    await db.flush()
    await record_audit_log(
        db,
        action=action,
        resource_type="site_content",
        resource_id=item.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"key": key, "section": payload.section},
    )
    await db.commit()
    await db.refresh(item)
    return SiteContentOut.model_validate(item)


@router.delete("/{key}", response_model=MessageResponse, summary="Supprimer un bloc de contenu (admin)")
async def delete_content(
    key: str,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    res = await db.execute(select(SiteContent).where(SiteContent.key == key))
    item = res.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Contenu introuvable.")
    item_id = item.id
    await db.delete(item)
    await record_audit_log(
        db,
        action="contents.deleted",
        resource_type="site_content",
        resource_id=item_id,
        actor=admin,
        ip_address=client_ip(request),
        details={"key": key},
    )
    await db.commit()
    return MessageResponse(message="Contenu supprimé.")

