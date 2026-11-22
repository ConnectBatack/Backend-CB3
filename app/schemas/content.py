import re
from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import ProjectStatus, SubscriptionStatus

_HTML_TAG_RE = re.compile(r"<\s*/?\s*[a-zA-Z][^>]*>")


def _assert_plain_text_structure(value: Any) -> Any:
    """Rejette toute balise HTML dans les contenus CMS / projets (affichage texte brut uniquement)."""
    if isinstance(value, str):
        if _HTML_TAG_RE.search(value):
            raise ValueError("Les balises HTML ne sont pas autorisées dans les contenus texte.")
        return value
    if isinstance(value, list):
        return [_assert_plain_text_structure(item) for item in value]
    if isinstance(value, dict):
        return {str(k): _assert_plain_text_structure(v) for k, v in value.items()}
    return value


class ProjectBase(BaseModel):
    slug: str | None = Field(default=None, min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    category: dict[str, str] = Field(default_factory=dict)
    title: dict[str, str]
    tagline: dict[str, str] = Field(default_factory=dict)
    description: dict[str, str] = Field(default_factory=dict)
    photo_id: str | None = None
    photo_slug: str | None = Field(default=None, max_length=120)
    target_amount_eur: Decimal = Field(ge=0, le=Decimal("1000000000"))
    is_featured_need: bool = False
    display_order: int = Field(default=0, ge=0)
    status: ProjectStatus = ProjectStatus.ACTIVE

    @field_validator("category", "title", "tagline", "description")
    @classmethod
    def check_plain_text(cls, v: dict[str, str]) -> dict[str, str]:
        return _assert_plain_text_structure(v)


class ProjectCreate(ProjectBase):
    source_language: Literal["fr", "en", "es", "nl"] | None = None
    auto_translate: bool = False
    overwrite_translations: bool = True


class ProjectUpdate(BaseModel):
    category: dict[str, str] | None = None
    title: dict[str, str] | None = None
    tagline: dict[str, str] | None = None
    description: dict[str, str] | None = None
    photo_id: str | None = None
    photo_slug: str | None = Field(default=None, max_length=120)
    target_amount_eur: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000"))
    is_featured_need: bool | None = None
    display_order: int | None = Field(default=None, ge=0)
    status: ProjectStatus | None = None
    source_language: Literal["fr", "en", "es", "nl"] | None = None
    auto_translate: bool = False
    overwrite_translations: bool = True

    @field_validator("category", "title", "tagline", "description")
    @classmethod
    def check_plain_text(cls, v: dict[str, str] | None) -> dict[str, str] | None:
        if v is not None:
            return _assert_plain_text_structure(v)
        return v


class ProjectTranslateRequest(BaseModel):
    source_language: Literal["fr", "en", "es", "nl"] = "fr"
    category: str = Field(default="", max_length=120)
    title: str = Field(default="", max_length=200)
    tagline: str = Field(default="", max_length=300)
    description: str = Field(default="", max_length=4000)

    @field_validator("category", "title", "tagline", "description")
    @classmethod
    def check_plain_text(cls, v: str) -> str:
        return _assert_plain_text_structure(v)


class ProjectTranslateResponse(BaseModel):
    category: dict[str, str]
    title: dict[str, str]
    tagline: dict[str, str]
    description: dict[str, str]
    translated_with_google: bool = True


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    slug: str
    category: dict[str, str]
    title: dict[str, str]
    tagline: dict[str, str]
    description: dict[str, str]
    photo_id: str | None
    photo_slug: str | None
    photo_url: str | None = None
    target_amount_eur: Decimal
    collected_amount_eur: Decimal
    contributors_count: int
    progress_percent: int = 0
    is_featured_need: bool
    display_order: int
    status: ProjectStatus
    created_at: datetime
    updated_at: datetime


class SiteContentUpsert(BaseModel):
    section: str = Field(min_length=1, max_length=64)
    content: dict[str, Any]
    media_id: str | None = None
    is_published: bool = True

    @field_validator("content")
    @classmethod
    def check_plain_text_content(cls, v: dict[str, Any]) -> dict[str, Any]:
        return _assert_plain_text_structure(v)


class SiteContentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    key: str
    section: str
    content: dict[str, Any]
    media_id: str | None
    is_published: bool
    updated_by_id: str | None
    created_at: datetime
    updated_at: datetime


class SubscriptionCreate(BaseModel):
    email: EmailStr
    projet: str = Field(min_length=2, max_length=64)
    langue: Literal["fr", "en", "es", "nl"] = "fr"
    consentement: bool

    @field_validator("consentement")
    @classmethod
    def must_consent(cls, v: bool) -> bool:
        if v is not True:
            raise ValueError("Le consentement explicite est obligatoire.")
        return v


class SubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    email: EmailStr
    project_id: str
    language: str
    consent_given: bool
    consent_date: datetime
    status: SubscriptionStatus
    confirmed_at: datetime | None
    unsubscribed_at: datetime | None
    created_at: datetime


class SubscriptionActionResponse(BaseModel):
    message: str
    status: SubscriptionStatus
