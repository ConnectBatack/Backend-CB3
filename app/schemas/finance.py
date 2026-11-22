from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.models.enums import (
    Currency,
    DonationFrequency,
    DonationStatus,
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
    PaymentMethod,
)


class PaymentAccountInfoOut(BaseModel):
    """Coordonnées du COVIDEBA et numéros de compte pour effectuer un don direct."""

    covideba_email: str
    covideba_phone: str
    bank_holder: str
    bank_name: str
    bank_account: str
    mobile_money_mtn: str
    mobile_money_orange: str
    postal_address: str | None = None
    other_info: str | None = None
    parity_fcfa_per_eur: Decimal


class OrganizationSettingsOut(PaymentAccountInfoOut):
    """Paramètres complets de l'organisation avec date de dernière modification."""

    updated_at: datetime | None = None


class OrganizationSettingsUpdate(BaseModel):
    """Mise à jour des coordonnées publiques et des numéros de compte par l'administrateur."""

    covideba_email: EmailStr | None = None
    covideba_phone: str | None = Field(default=None, min_length=4, max_length=64)
    bank_holder: str | None = Field(default=None, min_length=2, max_length=160)
    bank_name: str | None = Field(default=None, min_length=2, max_length=160)
    bank_account: str | None = Field(default=None, min_length=4, max_length=120)
    mobile_money_mtn: str | None = Field(default=None, min_length=4, max_length=64)
    mobile_money_orange: str | None = Field(default=None, min_length=4, max_length=64)
    postal_address: str | None = Field(default=None, max_length=255)
    other_info: str | None = Field(default=None, max_length=1000)


class DonationReceiptSubmitResponse(BaseModel):
    donation_id: str
    status: DonationStatus
    receipt_reference: str
    receipt_media_id: str
    amount_fcfa: int
    amount_eur: Decimal
    message: str


class DonationManualCreate(BaseModel):
    project_slug: str | None = Field(default=None, max_length=64)
    amount: Decimal = Field(gt=0, le=Decimal("1000000000"))
    currency: Currency = Currency.EUR
    payment_method: Literal["mobile_money", "especes", "virement"]
    donor_first_name: str = Field(default="", max_length=100)
    donor_last_name: str = Field(min_length=1, max_length=100)
    donor_email: EmailStr | None = None
    donor_phone: str | None = Field(default=None, max_length=32)
    tax_receipt_requested: bool = False
    language: Literal["fr", "en", "es", "nl"] = "fr"
    receipt_reference: str | None = Field(default=None, max_length=120)
    account: str = Field(default="Mobile Money", max_length=120)
    entry_date: date | None = None


class DonationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str | None
    amount_eur: Decimal
    original_amount: Decimal
    currency: Currency
    frequency: DonationFrequency
    payment_method: PaymentMethod
    status: DonationStatus
    donor_first_name: str
    donor_last_name: str
    donor_email: EmailStr | None
    donor_phone: str | None
    donor_country: str | None
    tax_receipt_requested: bool
    language: str
    receipt_reference: str | None
    receipt_media_id: str | None
    paid_at: datetime | None
    created_at: datetime


class LedgerEntryCreate(BaseModel):
    entry_date: date
    label: str = Field(min_length=2, max_length=255)
    account: str = Field(min_length=2, max_length=120)
    direction: LedgerDirection
    amount: Decimal = Field(gt=0, le=Decimal("1000000000"))
    currency: Currency = Currency.EUR
    project_id: str | None = None
    justificatif_media_id: str | None = None
    receipt_number: str | None = Field(default=None, max_length=120)
    corrects_entry_id: str | None = None


class LedgerEntryRejectRequest(BaseModel):
    reason: str = Field(min_length=3, max_length=500)


class LedgerEntryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    entry_number: int
    entry_type: LedgerEntryType
    entry_date: date
    label: str
    account: str
    direction: LedgerDirection
    amount: Decimal
    currency: Currency
    amount_eur_equiv: Decimal
    amount_fcfa_equiv: int
    status: LedgerStatus
    project_id: str | None
    donation_id: str | None
    transfer_id: str | None
    justificatif_media_id: str | None
    receipt_number: str | None
    corrects_entry_id: str | None
    target_entry_id: str | None
    created_by_id: str | None
    validated_by_id: str | None
    validated_at: datetime | None
    rejection_reason: str | None
    previous_hash: str
    entry_hash: str
    created_at: datetime


class IntegrityCheckOut(BaseModel):
    valid: bool
    entries_checked: int
    broken_at_entry_number: int | None = None
    detail: str


class TransferCreate(BaseModel):
    source_project_id: str | None = None
    destination_project_id: str | None = None
    source_account: str = Field(min_length=2, max_length=120)
    destination_account: str = Field(min_length=2, max_length=120)
    amount: Decimal = Field(gt=0, le=Decimal("1000000000"))
    currency: Currency = Currency.EUR
    reason: str = Field(min_length=3, max_length=1000)
    transfer_date: date


class TransferOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    source_project_id: str | None
    destination_project_id: str | None
    source_account: str
    destination_account: str
    amount: Decimal
    currency: Currency
    reason: str
    is_repaid: bool
    repaid_at: datetime | None
    transfer_date: date
    created_by_id: str | None
    created_at: datetime


class RegistrySummaryOut(BaseModel):
    total_validated_in_eur: Decimal
    total_validated_out_eur: Decimal
    available_balance_eur: Decimal
    available_balance_fcfa: int
    pending_countersign_count: int
    validated_count: int
    rejected_count: int
    balances_by_account_eur: dict[str, Decimal]
    parity_fcfa_per_eur: Decimal


class PublicLedgerItemOut(BaseModel):
    entry_number: int
    entry_date: date
    label: str
    account: str
    direction: LedgerDirection
    amount_eur_equiv: Decimal
    entry_hash: str
    justificatif_url: str | None = None


class TransparencyOut(BaseModel):
    total_collected_eur: Decimal
    total_spent_eur: Decimal
    available_balance_eur: Decimal
    donors_count: int
    projects_count: int
    parity_fcfa_per_eur: Decimal
    recent_entries: list[PublicLedgerItemOut]
