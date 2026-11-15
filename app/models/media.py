import uuid
from datetime import UTC, datetime

from sqlalchemy import JSON, BigInteger, Boolean, DateTime, Enum, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import MediaCategory

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class MediaAsset(Base):
    """Métadonnées des médias stockés dans le dossier local du backend (photos, justificatifs, documents)."""

    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True, index=True)
    category: Mapped[MediaCategory] = mapped_column(
        Enum(MediaCategory, values_callable=lambda e: [x.value for x in e], name="media_category"),
        nullable=False,
        default=MediaCategory.PHOTO,
        index=True,
    )
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=False)
    storage_path: Mapped[str] = mapped_column(String(512), unique=True, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(100), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    width: Mapped[int | None] = mapped_column(Integer, nullable=True)
    height: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Textes alternatifs et légendes multilingues {"fr": "...", "en": "...", "es": "...", "nl": "..."}
    alt_texts: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    captions: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    is_public: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    uploaded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

