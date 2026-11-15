from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import MediaCategory


class MediaAssetUpdate(BaseModel):
    slug: str | None = Field(default=None, max_length=120)
    alt_texts: dict[str, str] | None = None
    captions: dict[str, str] | None = None
    is_public: bool | None = None


class MediaAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str | None
    category: MediaCategory
    filename: str
    original_filename: str
    storage_path: str
    mime_type: str
    size_bytes: int
    sha256: str
    width: int | None
    height: int | None
    alt_texts: dict[str, str]
    captions: dict[str, str]
    is_public: bool
    file_url: str
    uploaded_by_id: str | None
    created_at: datetime
    updated_at: datetime

