import json

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import CONTENT_EDITOR_ROLES, get_optional_user, require_roles
from app.core.config import get_settings
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.core.storage import LocalStorageManager
from app.models.enums import MediaCategory
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.common import MessageResponse, PaginatedResponse
from app.schemas.media import MediaAssetOut, MediaAssetUpdate
from app.services.audit import record_audit_log

router = APIRouter(prefix="/media", tags=["Médias & Fichiers locaux"])


def _to_media_out(asset: MediaAsset) -> MediaAssetOut:
    settings = get_settings()
    prefix = settings.API_V1_PREFIX.rstrip("/")
    return MediaAssetOut(
        id=asset.id,
        slug=asset.slug,
        category=asset.category,
        filename=asset.filename,
        original_filename=asset.original_filename,
        storage_path=asset.storage_path,
        mime_type=asset.mime_type,
        size_bytes=asset.size_bytes,
        sha256=asset.sha256,
        width=asset.width,
        height=asset.height,
        alt_texts=asset.alt_texts or {},
        captions=asset.captions or {},
        is_public=asset.is_public,
        file_url=f"{prefix}/media/files/{asset.id}",
        uploaded_by_id=asset.uploaded_by_id,
        created_at=asset.created_at,
        updated_at=asset.updated_at,
    )


def _parse_json_dict(raw: str | None, field_name: str) -> dict[str, str]:
    if not raw or not raw.strip():
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Le champ {field_name} doit être un objet JSON valide.",
        ) from exc
    if not isinstance(parsed, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Le champ {field_name} doit être un objet JSON.",
        )
    return {str(k): str(v) for k, v in parsed.items()}


@router.post("/upload", response_model=MediaAssetOut, status_code=status.HTTP_201_CREATED, summary="Téléverser un média en local")
async def upload_media(
    request: Request,
    file: UploadFile = File(...),
    category: MediaCategory = Form(default=MediaCategory.PHOTO),
    slug: str | None = Form(default=None),
    alt_texts: str | None = Form(default=None),
    captions: str | None = Form(default=None),
    is_public: bool = Form(default=True),
    current_user: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> MediaAssetOut:
    cleaned_slug = slug.strip().lower() if slug and slug.strip() else None
    if cleaned_slug:
        existing = await db.execute(select(MediaAsset).where(MediaAsset.slug == cleaned_slug))
        if existing.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ce slug média est déjà utilisé.")

    parsed_alts = _parse_json_dict(alt_texts, "alt_texts")
    parsed_captions = _parse_json_dict(captions, "captions")

    storage = LocalStorageManager()
    stored = await storage.save_upload(file, category=category)

    asset = MediaAsset(
        slug=cleaned_slug,
        category=category,
        filename=stored.filename,
        original_filename=stored.original_filename,
        storage_path=stored.relative_path,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        width=stored.width,
        height=stored.height,
        alt_texts=parsed_alts,
        captions=parsed_captions,
        is_public=is_public,
        uploaded_by_id=current_user.id,
    )
    db.add(asset)
    await db.flush()
    await record_audit_log(
        db,
        action="media.uploaded",
        resource_type="media_asset",
        resource_id=asset.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"category": category.value, "filename": stored.original_filename, "slug": cleaned_slug},
    )
    await db.commit()
    await db.refresh(asset)
    return _to_media_out(asset)


@router.get("", response_model=PaginatedResponse[MediaAssetOut], summary="Lister les médias")
async def list_media(
    category: MediaCategory | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[MediaAssetOut]:
    query = select(MediaAsset)
    count_query = select(func.count(MediaAsset.id))

    if current_user is None:
        query = query.where(MediaAsset.is_public.is_(True))
        count_query = count_query.where(MediaAsset.is_public.is_(True))
    if category is not None:
        query = query.where(MediaAsset.category == category)
        count_query = count_query.where(MediaAsset.category == category)

    total = (await db.execute(count_query)).scalar_one()
    res = await db.execute(query.order_by(MediaAsset.created_at.desc()).limit(limit).offset(offset))
    items = [_to_media_out(a) for a in res.scalars().all()]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/by-slug/{slug}", response_model=MediaAssetOut, summary="Métadonnées d'un média par slug")
async def get_media_by_slug(
    slug: str,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> MediaAssetOut:
    res = await db.execute(select(MediaAsset).where(MediaAsset.slug == slug))
    asset = res.scalar_one_or_none()
    if asset is None or (not asset.is_public and current_user is None):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média introuvable.")
    return _to_media_out(asset)


@router.get("/files/{asset_id}", summary="Servir le fichier binaire depuis le dossier local")
async def serve_media_file(
    asset_id: str,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    res = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média introuvable.")
    if not asset.is_public and current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentification requise pour ce fichier.")

    storage = LocalStorageManager()
    file_path = storage.resolve_safe_path(asset.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fichier absent du stockage local.")

    return FileResponse(
        path=file_path,
        media_type=asset.mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=3600" if asset.is_public else "private, no-store",
        },
    )


@router.get("/files-by-slug/{slug}", summary="Servir le fichier binaire par slug")
async def serve_media_file_by_slug(
    slug: str,
    current_user: User | None = Depends(get_optional_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    res = await db.execute(select(MediaAsset).where(MediaAsset.slug == slug))
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média introuvable.")
    if not asset.is_public and current_user is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentification requise.")

    storage = LocalStorageManager()
    file_path = storage.resolve_safe_path(asset.storage_path)
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Fichier absent du stockage local.")

    return FileResponse(
        path=file_path,
        media_type=asset.mime_type,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "public, max-age=3600" if asset.is_public else "private, no-store",
        },
    )


@router.patch("/{asset_id}", response_model=MediaAssetOut, summary="Mettre à jour les métadonnées d'un média")
async def update_media_metadata(
    asset_id: str,
    payload: MediaAssetUpdate,
    request: Request,
    current_user: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> MediaAssetOut:
    res = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média introuvable.")

    if payload.slug is not None:
        cleaned = payload.slug.strip().lower() or None
        if cleaned and cleaned != asset.slug:
            dup = await db.execute(select(MediaAsset).where(MediaAsset.slug == cleaned))
            if dup.scalar_one_or_none() is not None:
                raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ce slug média est déjà utilisé.")
        asset.slug = cleaned
    if payload.alt_texts is not None:
        asset.alt_texts = payload.alt_texts
    if payload.captions is not None:
        asset.captions = payload.captions
    if payload.is_public is not None:
        asset.is_public = payload.is_public

    await record_audit_log(
        db,
        action="media.updated",
        resource_type="media_asset",
        resource_id=asset.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(asset)
    return _to_media_out(asset)


@router.delete("/{asset_id}", response_model=MessageResponse, summary="Supprimer un média du stockage local")
async def delete_media(
    asset_id: str,
    request: Request,
    current_user: User = Depends(require_roles(*CONTENT_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    res = await db.execute(select(MediaAsset).where(MediaAsset.id == asset_id))
    asset = res.scalar_one_or_none()
    if asset is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Média introuvable.")

    storage_path = asset.storage_path
    await db.delete(asset)
    await record_audit_log(
        db,
        action="media.deleted",
        resource_type="media_asset",
        resource_id=asset_id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"storage_path": storage_path},
    )
    await db.commit()

    LocalStorageManager().delete_file(storage_path)
    return MessageResponse(message="Média supprimé.")

