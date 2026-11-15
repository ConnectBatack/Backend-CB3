from datetime import UTC, date, datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, TREASURY_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip, rate_limiter
from app.models.content import Project
from app.models.enums import (
    DonationFrequency,
    DonationStatus,
    LedgerDirection,
    LedgerStatus,
    PaymentMethod,
)
from app.models.finance import Donation
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.finance import (
    DonationCheckoutRequest,
    DonationCheckoutResponse,
    DonationManualCreate,
    DonationOut,
)
from app.services.audit import record_audit_log
from app.services.ledger_service import convert_currency, create_ledger_entry
from app.services.stripe_service import create_stripe_checkout_for_donation

router = APIRouter(prefix="/donations", tags=["Dons & Paiements"])


@router.post("/checkout", response_model=DonationCheckoutResponse, summary="Démarrer un don Stripe Checkout")
async def create_checkout(
    payload: DonationCheckoutRequest,
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> DonationCheckoutResponse:
    await rate_limiter.check(f"checkout:{client_ip(request)}", max_requests=15, window_seconds=300)
    donation, url = await create_stripe_checkout_for_donation(db, payload)
    return DonationCheckoutResponse(donation_id=donation.id, checkout_url=url)


@router.post("/manual", response_model=DonationOut, status_code=status.HTTP_201_CREATED, summary="Enregistrer un don manuel (espèces, Mobile Money, virement)")
async def record_manual_donation(
    payload: DonationManualCreate,
    request: Request,
    current_user: User = Depends(require_roles(*TREASURY_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> DonationOut:
    project: Project | None = None
    if payload.project_slug:
        res = await db.execute(select(Project).where(Project.slug == payload.project_slug))
        project = res.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")

    eur_equiv, _ = convert_currency(payload.amount, payload.currency)
    now = datetime.now(UTC)
    method = PaymentMethod(payload.payment_method)

    donation = Donation(
        project_id=project.id if project else None,
        amount_eur=eur_equiv,
        original_amount=Decimal(payload.amount).quantize(Decimal("0.01")),
        currency=payload.currency,
        frequency=DonationFrequency.PONCTUEL,
        payment_method=method,
        status=DonationStatus.PAID,
        donor_first_name=payload.donor_first_name.strip(),
        donor_last_name=payload.donor_last_name.strip(),
        donor_email=payload.donor_email.lower().strip(),
        tax_receipt_requested=payload.tax_receipt_requested,
        terms_accepted=True,
        language=payload.language,
        receipt_reference=payload.receipt_reference,
        recorded_by_id=current_user.id,
        paid_at=now,
    )
    db.add(donation)
    await db.flush()

    if project is not None:
        project.collected_amount_eur = Decimal(project.collected_amount_eur) + eur_equiv
        project.contributors_count += 1

    # L'écriture manuelle démarre à "a_contresigner" pour garantir la double vérification
    await create_ledger_entry(
        db,
        entry_date=payload.entry_date or date.today(),
        label=f"Don manuel ({method.value}) - {donation.donor_last_name}",
        account=payload.account,
        direction=LedgerDirection.ENTREE,
        amount=payload.amount,
        currency=payload.currency,
        initial_status=LedgerStatus.A_CONTRESIGNER,
        project_id=project.id if project else None,
        donation_id=donation.id,
        receipt_number=payload.receipt_reference,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await record_audit_log(
        db,
        action="donations.manual_recorded",
        resource_type="donation",
        resource_id=donation.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"amount_eur": str(eur_equiv), "method": method.value},
    )
    await db.commit()
    await db.refresh(donation)
    return DonationOut.model_validate(donation)


@router.get("", response_model=PaginatedResponse[DonationOut], summary="Lister les dons (espace gestion)")
async def list_donations(
    status_filter: DonationStatus | None = Query(default=None, alias="status"),
    project_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[DonationOut]:
    query = select(Donation)
    count_query = select(func.count(Donation.id))
    if status_filter is not None:
        query = query.where(Donation.status == status_filter)
        count_query = count_query.where(Donation.status == status_filter)
    if project_id is not None:
        query = query.where(Donation.project_id == project_id)
        count_query = count_query.where(Donation.project_id == project_id)

    total = (await db.execute(count_query)).scalar_one()
    res = await db.execute(query.order_by(Donation.created_at.desc()).limit(limit).offset(offset))
    items = [DonationOut.model_validate(d) for d in res.scalars().all()]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/{donation_id}", response_model=DonationOut, summary="Détail d'un don")
async def get_donation(
    donation_id: str,
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> DonationOut:
    res = await db.execute(select(Donation).where(Donation.id == donation_id))
    donation = res.scalar_one_or_none()
    if donation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Don introuvable.")
    return DonationOut.model_validate(donation)

