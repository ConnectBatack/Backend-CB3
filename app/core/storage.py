import hashlib
import io
import uuid
from dataclasses import dataclass
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from PIL import Image, UnidentifiedImageError

from app.core.config import get_settings
from app.models.enums import MediaCategory

ALLOWED_IMAGE_MIMES: dict[str, str] = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/avif": ".avif",
}

ALLOWED_DOCUMENT_MIMES: dict[str, str] = {
    "application/pdf": ".pdf",
    "text/plain": ".txt",
    "text/csv": ".csv",
    "application/json": ".json",
    **ALLOWED_IMAGE_MIMES,
}

CATEGORY_SUBDIRS: dict[MediaCategory, str] = {
    MediaCategory.PHOTO: "photos",
    MediaCategory.LOGO: "logos",
    MediaCategory.JUSTIFICATIF: "justificatifs",
    MediaCategory.DOCUMENT: "documents",
    MediaCategory.AUTRE: "autres",
}


@dataclass
class StoredFileInfo:
    filename: str
    original_filename: str
    relative_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    width: int | None
    height: int | None


class LocalStorageManager:
    """Gestionnaire sécurisé de stockage local des médias (photos, justificatifs, documents)."""

    def __init__(self, base_dir: Path | None = None) -> None:
        self.base_dir = (base_dir or get_settings().storage_path).resolve()

    def ensure_directories(self) -> None:
        self.base_dir.mkdir(parents=True, exist_ok=True)
        for subdir in CATEGORY_SUBDIRS.values():
            (self.base_dir / subdir).mkdir(parents=True, exist_ok=True)

    def resolve_safe_path(self, relative_path: str) -> Path:
        """Résout un chemin relatif en empêchant toute traversée de répertoire."""
        target = (self.base_dir / relative_path).resolve()
        if not target.is_relative_to(self.base_dir):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Chemin de fichier invalide.",
            )
        return target

    def _validate_and_inspect(
        self, data: bytes, content_type: str, category: MediaCategory
    ) -> tuple[str, str, int | None, int | None]:
        is_image_only = category in (MediaCategory.PHOTO, MediaCategory.LOGO)
        allowed = ALLOWED_IMAGE_MIMES if is_image_only else ALLOWED_DOCUMENT_MIMES

        mime = (content_type or "").split(";")[0].strip().lower()
        if mime == "image/jpg":
            mime = "image/jpeg"

        if mime not in allowed:
            raise HTTPException(
                status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
                detail=f"Type de fichier non autorisé ({mime or 'inconnu'}).",
            )

        width: int | None = None
        height: int | None = None

        if mime in ALLOWED_IMAGE_MIMES:
            try:
                with Image.open(io.BytesIO(data)) as img:
                    img.verify()
                with Image.open(io.BytesIO(data)) as img:
                    width, height = img.size
            except (UnidentifiedImageError, OSError) as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Le fichier image est corrompu ou invalide.",
                ) from exc
        elif mime == "application/pdf":
            if not data.startswith(b"%PDF-"):
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Le fichier PDF est invalide.",
                )

        return mime, allowed[mime], width, height

    async def save_upload(
        self, upload: UploadFile, category: MediaCategory, max_bytes: int | None = None
    ) -> StoredFileInfo:
        """Valide, inspecte et enregistre un fichier téléversé dans le dossier local."""
        self.ensure_directories()
        limit = max_bytes or get_settings().MAX_UPLOAD_SIZE_BYTES
        data = await upload.read(limit + 1)
        if not data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Le fichier envoyé est vide.",
            )
        if len(data) > limit:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Fichier trop volumineux (max {limit // (1024 * 1024)} Mo).",
            )

        mime, ext, width, height = self._validate_and_inspect(
            data, upload.content_type or "", category
        )
        return self.save_bytes(
            data=data,
            original_filename=upload.filename or f"fichier{ext}",
            mime_type=mime,
            ext=ext,
            category=category,
            width=width,
            height=height,
        )

    def save_bytes(
        self,
        data: bytes,
        original_filename: str,
        mime_type: str,
        ext: str,
        category: MediaCategory,
        width: int | None = None,
        height: int | None = None,
    ) -> StoredFileInfo:
        """Enregistre des octets déjà validés sur le disque local."""
        self.ensure_directories()
        subdir = CATEGORY_SUBDIRS[category]
        unique_name = f"{uuid.uuid4().hex}{ext}"
        rel_path = f"{subdir}/{unique_name}"
        dest = self.resolve_safe_path(rel_path)
        dest.write_bytes(data)

        digest = hashlib.sha256(data).hexdigest()
        safe_original = Path(original_filename).name[:255] or unique_name
        return StoredFileInfo(
            filename=unique_name,
            original_filename=safe_original,
            relative_path=rel_path,
            mime_type=mime_type,
            size_bytes=len(data),
            sha256=digest,
            width=width,
            height=height,
        )

    def delete_file(self, relative_path: str) -> None:
        target = self.resolve_safe_path(relative_path)
        if target.exists() and target.is_file():
            target.unlink()

