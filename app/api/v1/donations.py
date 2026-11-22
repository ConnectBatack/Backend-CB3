import re
from datetime import date
from decimal import Decimal
from typing import Literal

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
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, TREASURY_ROLES, require_roles
from app.core.config import SUPPORTED_LANGUAGES
from app.core.database import get_db
from app.core.rate_limit import client_ip, rate_limiter
from app.core.storage import LocalStorageManager
from app.models.content import Project
from app.models.enums import (
    Currency,
    DonationFrequency,
    DonationStatus,
    LedgerDirection,
    LedgerStatus,
    MediaCategory,
    PaymentMethod,
)
from app.models.finance import Donation
from app.models.media import MediaAsset
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.finance import (
    DonationManualCreate,
    DonationOut,
    DonationReceiptSubmitResponse,
    PaymentAccountInfoOut,
)
from app.services.audit import record_audit_log
from app.services.ledger_service import convert_currency, create_ledger_entry, ledger_write_lock
from app.services.settings_service import get_effective_organization_settings

router = APIRouter(prefix="/donations", tags=["Dons & Dépôts avec reçu"])

_RE_EMAIL = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DON_MONTANT_MIN_FCFA = 5000
DON_MONTANT_MAX_FCFA = 655000000


@router.get("/accounts", response_model=PaymentAccountInfoOut, summary="Coordonnées et comptes de dépôt du COVIDEBA")
async def get_payment_accounts(db: AsyncSession = Depends(get_db)) -> PaymentAccountInfoOut:
    org = await get_effective_organization_settings(db)
    return PaymentAccountInfoOut(**org.model_dump(exclude={"updated_at"}))


@router.post(
    "/submit-receipt",
    response_model=DonationReceiptSubmitResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Soumettre un reçu de dépôt/virement pour confirmer un don",
)
async def submit_donation_receipt(
    request: Request,
    montant_fcfa: int = Form(...),
    moyen: Literal["virement", "mobile_money", "depot_bancaire"] = Form(...),
    receipt_reference: str = Form(..., min_length=2, max_length=120),
    nom: str = Form(..., min_length=1, max_length=100),
    prenom: str = Form(default="", max_length=100),
    email: str = Form(default="", max_length=254),
    telephone: str = Form(default="", max_length=32),
    pays: Literal["BE", "autre"] = Form(default="autre"),
    attestation: bool = Form(default=False),
    conditions: bool = Form(...),
    langue: str = Form(default="fr"),
    projet: str = Form(default="", max_length=64),
    receipt_file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
) -> DonationReceiptSubmitResponse:
    """
    Reçoit la confirmation d'un don effectué directement sur le compte bancaire
    ou Mobile Money du COVIDEBA : enregistre le reçu dans storage/justificatifs/
    (privé) et crée une écriture `a_contresigner` en attente de vérification par le trésorier.
    """
    await rate_limiter.check(f"submit_receipt:{client_ip(request)}", max_requests=12, window_seconds=300)

    if conditions is not True:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Les conditions doivent être acceptées.")
    if langue not in SUPPORTED_LANGUAGES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Langue non supportée.")
    if montant_fcfa < DON_MONTANT_MIN_FCFA or montant_fcfa > DON_MONTANT_MAX_FCFA:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Le montant doit être compris entre {DON_MONTANT_MIN_FCFA} et {DON_MONTANT_MAX_FCFA} FCFA.",
        )

    cleaned_email = email.strip().lower()
    cleaned_phone = telephone.strip()
    if cleaned_email and not _RE_EMAIL.match(cleaned_email):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Adresse e-mail invalide.")
    if not cleaned_email and not cleaned_phone:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Indiquez une adresse e-mail ou un numéro de téléphone.",
        )
    if attestation and pays != "BE":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="L'attestation fiscale est réservée aux donateurs résidant en Belgique.",
        )

    project: Project | None = None
    if projet.strip():
        p_res = await db.execute(select(Project).where(Project.slug == projet.strip()))
        project = p_res.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet inconnu.")

    # Enregistrement sécurisé du reçu dans storage/justificatifs/ (privé par défaut)
    storage = LocalStorageManager()
    stored = await storage.save_upload(receipt_file, category=MediaCategory.JUSTIFICATIF)

    asset = MediaAsset(
        category=MediaCategory.JUSTIFICATIF,
        filename=stored.filename,
        original_filename=stored.original_filename,
        storage_path=stored.relative_path,
        mime_type=stored.mime_type,
        size_bytes=stored.size_bytes,
        sha256=stored.sha256,
        width=stored.width,
        height=stored.height,
        alt_texts={},
        captions={},
        is_public=False,
    )
    db.add(asset)
    await db.flush()

    eur_equiv, fcfa_int = convert_currency(Decimal(montant_fcfa), Currency.FCFA)
    method = PaymentMethod(moyen)
    ref_clean = receipt_reference.strip()

    donation = Donation(
        project_id=project.id if project else None,
        amount_eur=eur_equiv,
        original_amount=Decimal(fcfa_int).quantize(Decimal("0.01")),
        currency=Currency.FCFA,
        frequency=DonationFrequency.PONCTUEL,
        payment_method=method,
        status=DonationStatus.PENDING,
        donor_first_name=prenom.strip(),
        donor_last_name=nom.strip(),
        donor_email=cleaned_email or None,
        donor_phone=cleaned_phone or None,
        donor_country=pays,
        tax_receipt_requested=attestation and pays == "BE",
        terms_accepted=True,
        language=langue,
        receipt_reference=ref_clean,
        receipt_media_id=asset.id,
    )
    db.add(donation)
    await db.flush()

    account_name = "Mobile Money" if method == PaymentMethod.MOBILE_MONEY else "Banque du COVIDEBA"
    method_labels = {
        PaymentMethod.VIREMENT: "Virement bancaire donateur (reçu soumis)",
        PaymentMethod.DEPOT_BANCAIRE: "Dépôt bancaire donateur (reçu soumis)",
        PaymentMethod.MOBILE_MONEY: "Dépôt Mobile Money donateur (reçu soumis)",
    }

    async with ledger_write_lock(db):
        await create_ledger_entry(
            db,
            entry_date=date.today(),
            label=method_labels.get(method, "Dépôt donateur (reçu soumis)"),
            account=account_name,
            direction=LedgerDirection.ENTREE,
            amount=Decimal(fcfa_int),
            currency=Currency.FCFA,
            initial_status=LedgerStatus.A_CONTRESIGNER,
            project_id=project.id if project else None,
            donation_id=donation.id,
            justificatif_media_id=asset.id,
            receipt_number=ref_clean,
            ip_address=client_ip(request),
        )
        await record_audit_log(
            db,
            action="donations.receipt_submitted",
            resource_type="donation",
            resource_id=donation.id,
            ip_address=client_ip(request),
            details={"amount_fcfa": fcfa_int, "method": method.value, "receipt_ref": ref_clean},
        )
        await db.commit()

    return DonationReceiptSubmitResponse(
        donation_id=donation.id,
        status=DonationStatus.PENDING,
        receipt_reference=ref_clean,
        receipt_media_id=asset.id,
        amount_fcfa=fcfa_int,
        amount_eur=eur_equiv,
        message="Reçu enregistré. Votre don sera confirmé après vérification par le trésorier du COVIDEBA.",
    )


@router.post(
    "/manual",
    response_model=DonationOut,
    status_code=status.HTTP_201_CREATED,
    summary="Enregistrer un don manuel (espèces, Mobile Money, virement — e-mail facultatif)",
)
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
    method = PaymentMethod(payload.payment_method)

    donation = Donation(
        project_id=project.id if project else None,
        amount_eur=eur_equiv,
        original_amount=Decimal(payload.amount).quantize(Decimal("0.01")),
        currency=payload.currency,
        frequency=DonationFrequency.PONCTUEL,
        payment_method=method,
        status=DonationStatus.PENDING,
        donor_first_name=payload.donor_first_name.strip(),
        donor_last_name=payload.donor_last_name.strip(),
        donor_email=payload.donor_email.lower().strip() if payload.donor_email else None,
        donor_phone=payload.donor_phone.strip() if payload.donor_phone else None,
        tax_receipt_requested=payload.tax_receipt_requested,
        terms_accepted=True,
        language=payload.language,
        receipt_reference=payload.receipt_reference,
        recorded_by_id=current_user.id,
        paid_at=None,
    )
    db.add(donation)
    await db.flush()

    method_labels = {
        PaymentMethod.ESPECES: "Don manuel (espèces)",
        PaymentMethod.MOBILE_MONEY: "Don manuel (Mobile Money)",
        PaymentMethod.VIREMENT: "Don manuel (virement)",
    }
    async with ledger_write_lock(db):
        await create_ledger_entry(
            db,
            entry_date=payload.entry_date or date.today(),
            label=method_labels.get(method, "Don manuel"),
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
