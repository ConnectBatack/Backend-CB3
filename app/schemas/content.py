from datetime import datetime
from decimal import Decimal
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import ProjectStatus, SubscriptionStatus


class ProjectBase(BaseModel):
    slug: str = Field(min_length=2, max_length=64, pattern=r"^[a-z0-9-]+$")
    category: dict[str, str] = Field(default_factory=dict)
    title: dict[str, str]
    tagline: dict[str, str] = Field(default_factory=dict)
    description: dict[str, str] = Field(default_factory=dict)
    photo_id: str | None = None
    photo_slug: str | None = Field(default=None, max_length=120)
    target_amount_eur: Decimal = Field(ge=0, le=Decimal("1000000000"))
    collected_amount_eur: Decimal = Field(default=Decimal("0.00"), ge=0, le=Decimal("1000000000"))
    contributors_count: int = Field(default=0, ge=0)
    is_featured_need: bool = False
    display_order: int = Field(default=0, ge=0)
    status: ProjectStatus = ProjectStatus.ACTIVE


class ProjectCreate(ProjectBase):
    pass


class ProjectUpdate(BaseModel):
    category: dict[str, str] | None = None
    title: dict[str, str] | None = None
    tagline: dict[str, str] | None = None
    description: dict[str, str] | None = None
    photo_id: str | None = None
    photo_slug: str | None = Field(default=None, max_length=120)
    target_amount_eur: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000"))
    collected_amount_eur: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000"))
    contributors_count: int | None = Field(default=None, ge=0)
    is_featured_need: bool | None = None
    display_order: int | None = Field(default=None, ge=0)
    status: ProjectStatus | None = None


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
    confirm_token: str | None = None
    unsubscribe_token: str | None = None

