import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.enums import ProjectStatus, SubscriptionStatus

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class Project(Base):
    """Projets vitrine du village de Batack (dispensaire, batteries, école, eau, marché, route…)."""

    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    slug: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)

    # Contenu multilingue {"fr": "...", "en": "...", "es": "...", "nl": "..."}
    category: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    title: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    tagline: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    description: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)

    photo_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    photo_slug: Mapped[str | None] = mapped_column(String(120), nullable=True)

    target_amount_eur: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    collected_amount_eur: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False, default=Decimal("0.00"))
    contributors_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    is_featured_need: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    display_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[ProjectStatus] = mapped_column(
        Enum(ProjectStatus, values_callable=lambda e: [x.value for x in e], name="project_status"),
        nullable=False,
        default=ProjectStatus.ACTIVE,
    )

    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    photo = relationship("MediaAsset", foreign_keys=[photo_id])


class SiteContent(Base):
    """Blocs de contenu éditorial multilingue du site (accueil, histoire, équipe, pages légales, diaporama)."""

    __tablename__ = "site_contents"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    key: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    section: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    content: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)
    media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    is_published: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    updated_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )

    media = relationship("MediaAsset", foreign_keys=[media_id])


class ProjectSubscription(Base):
    """Inscription aux nouvelles d'un projet (double opt-in + lien de désinscription RGPD)."""

    __tablename__ = "project_subscriptions"
    __table_args__ = (UniqueConstraint("email", "project_id", name="uq_subscription_email_project"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    email: Mapped[str] = mapped_column(String(254), nullable=False, index=True)
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False, index=True
    )
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="fr")
    consent_given: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    consent_date: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(
            SubscriptionStatus,
            values_callable=lambda e: [x.value for x in e],
            name="subscription_status",
        ),
        nullable=False,
        default=SubscriptionStatus.PENDING,
    )
    confirm_token_hash: Mapped[str | None] = mapped_column(String(64), unique=True, nullable=True)
    unsubscribe_token_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    unsubscribed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

