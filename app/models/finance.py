import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import (
    Currency,
    DonationFrequency,
    DonationStatus,
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    PaymentMethod,
)


def utcnow() -> datetime:
    return datetime.now(UTC)


class Donation(Base):
    """Dons en ligne (Stripe) ou manuels (espèces, Mobile Money, virement)."""

    __tablename__ = "donations"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    amount_eur: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    original_amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, values_callable=lambda e: [x.value for x in e], name="currency_enum"),
        nullable=False,
        default=Currency.EUR,
    )
    frequency: Mapped[DonationFrequency] = mapped_column(
        Enum(
            DonationFrequency,
            values_callable=lambda e: [x.value for x in e],
            name="donation_frequency",
        ),
        nullable=False,
        default=DonationFrequency.PONCTUEL,
    )
    payment_method: Mapped[PaymentMethod] = mapped_column(
        Enum(PaymentMethod, values_callable=lambda e: [x.value for x in e], name="payment_method"),
        nullable=False,
    )
    status: Mapped[DonationStatus] = mapped_column(
        Enum(DonationStatus, values_callable=lambda e: [x.value for x in e], name="donation_status"),
        nullable=False,
        default=DonationStatus.PENDING,
        index=True,
    )

    donor_first_name: Mapped[str] = mapped_column(String(100), nullable=False, default="")
    donor_last_name: Mapped[str] = mapped_column(String(100), nullable=False)
    donor_email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    donor_phone: Mapped[str | None] = mapped_column(String(32), nullable=True)
    donor_country: Mapped[str | None] = mapped_column(String(16), nullable=True)
    tax_receipt_requested: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    terms_accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    language: Mapped[str] = mapped_column(String(8), nullable=False, default="fr")

    stripe_customer_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stripe_session_id: Mapped[str | None] = mapped_column(String(120), unique=True, nullable=True, index=True)
    stripe_payment_intent_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    stripe_subscription_id: Mapped[str | None] = mapped_column(String(120), nullable=True, index=True)
    receipt_reference: Mapped[str | None] = mapped_column(String(120), nullable=True)
    receipt_media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )

    recorded_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class StripeEvent(Base):
    """Table d'idempotence des webhooks Stripe (un événement Stripe ne doit jamais être traité deux fois)."""

    __tablename__ = "stripe_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    event_id: Mapped[str] = mapped_column(String(120), unique=True, nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    processed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class LedgerEntry(Base):
    """
    Registre financier strictement immuable (append-only) et chaîné par empreinte SHA-256.
    Aucune ligne n'est jamais modifiée ni supprimée :
    - une validation ou un rejet est enregistré comme une nouvelle écriture chaînée (target_entry_id),
    - une erreur de montant/libellé est corrigée par une écriture compensatoire (corrects_entry_id).
    """

    __tablename__ = "ledger_entries"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    entry_number: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    entry_type: Mapped[LedgerEntryType] = mapped_column(
        Enum(LedgerEntryType, values_callable=lambda e: [x.value for x in e], name="ledger_entry_type"),
        nullable=False,
        default=LedgerEntryType.OPERATION,
        index=True,
    )
    entry_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    account: Mapped[str] = mapped_column(String(120), nullable=False, index=True)
    direction: Mapped[LedgerDirection] = mapped_column(
        Enum(LedgerDirection, values_callable=lambda e: [x.value for x in e], name="ledger_direction"),
        nullable=False,
    )
    # Montants exacts (jamais de flottants)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, values_callable=lambda e: [x.value for x in e], name="currency_enum"),
        nullable=False,
        default=Currency.EUR,
    )
    amount_eur_equiv: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    amount_fcfa_equiv: Mapped[int] = mapped_column(BigInteger, nullable=False)

    # Statut initial figé à la création et couvert par l'empreinte SHA-256
    status: Mapped[LedgerStatus] = mapped_column(
        Enum(LedgerStatus, values_callable=lambda e: [x.value for x in e], name="ledger_status"),
        nullable=False,
        default=LedgerStatus.A_CONTRESIGNER,
        index=True,
    )

    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    donation_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("donations.id", ondelete="SET NULL"), nullable=True
    )
    transfer_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("fund_transfers.id", ondelete="SET NULL"), nullable=True, index=True
    )
    justificatif_media_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True
    )
    receipt_number: Mapped[str | None] = mapped_column(String(120), nullable=True)
    corrects_entry_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ledger_entries.id", ondelete="SET NULL"), nullable=True
    )
    target_entry_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("ledger_entries.id", ondelete="SET NULL"), nullable=True, index=True
    )

    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    validated_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    rejection_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    previous_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    entry_hash: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class FundTransfer(Base):
    """Transferts ou prêts internes entre projets/rubriques, avec suivi de remboursement."""

    __tablename__ = "fund_transfers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    source_project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    destination_project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True
    )
    source_account: Mapped[str] = mapped_column(String(120), nullable=False)
    destination_account: Mapped[str] = mapped_column(String(120), nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    currency: Mapped[Currency] = mapped_column(
        Enum(Currency, values_callable=lambda e: [x.value for x in e], name="currency_enum"),
        nullable=False,
        default=Currency.EUR,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    is_repaid: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repaid_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    transfer_date: Mapped[date] = mapped_column(Date, nullable=False)
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
