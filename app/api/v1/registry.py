import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, REGISTRY_ENTRY_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.enums import LedgerDirection, LedgerStatus, UserRole
from app.models.finance import LedgerEntry
from app.models.user import User
from app.schemas.common import PaginatedResponse
from app.schemas.finance import (
    IntegrityCheckOut,
    LedgerEntryCreate,
    LedgerEntryOut,
    LedgerEntryRejectRequest,
    RegistrySummaryOut,
)
from app.services.ledger_service import (
    compute_registry_summary,
    create_ledger_entry,
    reject_ledger_entry,
    validate_ledger_entry,
    verify_ledger_integrity,
)

router = APIRouter(prefix="/registry", tags=["Registre financier chaîné"])


@router.get("/entries", response_model=PaginatedResponse[LedgerEntryOut], summary="Lister les écritures du registre")
async def list_entries(
    status_filter: LedgerStatus | None = Query(default=None, alias="status"),
    direction: LedgerDirection | None = None,
    account: str | None = None,
    project_id: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[LedgerEntryOut]:
    query = select(LedgerEntry)
    count_query = select(func.count(LedgerEntry.id))
    if status_filter is not None:
        query = query.where(LedgerEntry.status == status_filter)
        count_query = count_query.where(LedgerEntry.status == status_filter)
    if direction is not None:
        query = query.where(LedgerEntry.direction == direction)
        count_query = count_query.where(LedgerEntry.direction == direction)
    if account:
        query = query.where(LedgerEntry.account == account)
        count_query = count_query.where(LedgerEntry.account == account)
    if project_id:
        query = query.where(LedgerEntry.project_id == project_id)
        count_query = count_query.where(LedgerEntry.project_id == project_id)

    total = (await db.execute(count_query)).scalar_one()
    res = await db.execute(query.order_by(LedgerEntry.entry_number.desc()).limit(limit).offset(offset))
    items = [LedgerEntryOut.model_validate(e) for e in res.scalars().all()]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)


@router.get("/summary", response_model=RegistrySummaryOut, summary="Soldes et indicateurs du registre")
async def get_summary(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> RegistrySummaryOut:
    data = await compute_registry_summary(db)
    return RegistrySummaryOut(**data)


@router.post("/entries", response_model=LedgerEntryOut, status_code=status.HTTP_201_CREATED, summary="Créer une écriture immuable")
async def create_entry(
    payload: LedgerEntryCreate,
    request: Request,
    current_user: User = Depends(require_roles(*REGISTRY_ENTRY_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> LedgerEntryOut:
    # Un membre du COVIDEBA peut soumettre une entrée (ex: Mobile Money), seul le trésorier/admin saisit les dépenses
    if current_user.role == UserRole.MEMBRE and payload.direction == LedgerDirection.SORTIE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le trésorier ou un administrateur peut enregistrer une dépense.",
        )

    entry = await create_ledger_entry(
        db,
        entry_date=payload.entry_date,
        label=payload.label,
        account=payload.account,
        direction=payload.direction,
        amount=payload.amount,
        currency=payload.currency,
        initial_status=LedgerStatus.A_CONTRESIGNER,
        project_id=payload.project_id,
        justificatif_media_id=payload.justificatif_media_id,
        receipt_number=payload.receipt_number,
        corrects_entry_id=payload.corrects_entry_id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(entry)
    return LedgerEntryOut.model_validate(entry)


@router.post("/entries/{entry_id}/validate", response_model=LedgerEntryOut, summary="Contresigner / valider une écriture")
async def validate_entry_endpoint(
    entry_id: str,
    request: Request,
    current_user: User = Depends(require_roles(*REGISTRY_ENTRY_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> LedgerEntryOut:
    res = await db.execute(select(LedgerEntry).where(LedgerEntry.id == entry_id))
    entry = res.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Écriture introuvable.")
    updated = await validate_ledger_entry(db, entry=entry, actor=current_user, ip_address=client_ip(request))
    return LedgerEntryOut.model_validate(updated)


@router.post("/entries/{entry_id}/reject", response_model=LedgerEntryOut, summary="Rejeter une écriture avec motif")
async def reject_entry_endpoint(
    entry_id: str,
    payload: LedgerEntryRejectRequest,
    request: Request,
    current_user: User = Depends(require_roles(*REGISTRY_ENTRY_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> LedgerEntryOut:
    res = await db.execute(select(LedgerEntry).where(LedgerEntry.id == entry_id))
    entry = res.scalar_one_or_none()
    if entry is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Écriture introuvable.")
    updated = await reject_ledger_entry(
        db, entry=entry, reason=payload.reason, actor=current_user, ip_address=client_ip(request)
    )
    return LedgerEntryOut.model_validate(updated)


@router.get("/verify-integrity", response_model=IntegrityCheckOut, summary="Vérifier l'intégrité de la chaîne SHA-256")
async def verify_integrity_endpoint(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> IntegrityCheckOut:
    result = await verify_ledger_integrity(db)
    return IntegrityCheckOut(**result)


@router.get("/export", summary="Exporter le registre (CSV ou JSON)")
async def export_registry(
    fmt: Literal["csv", "json"] = Query(default="csv"),
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
):
    res = await db.execute(select(LedgerEntry).order_by(LedgerEntry.entry_number.asc()))
    entries = list(res.scalars().all())

    if fmt == "json":
        return [LedgerEntryOut.model_validate(e).model_dump(mode="json") for e in entries]

    buf = io.StringIO()
    writer = csv.writer(buf, delimiter=";")
    writer.writerow(
        [
            "numero",
            "date",
            "libelle",
            "compte",
            "direction",
            "montant",
            "devise",
            "equivalent_eur",
            "equivalent_fcfa",
            "etat",
            "justificatif",
            "previous_hash",
            "entry_hash",
        ]
    )
    for e in entries:
        writer.writerow(
            [
                e.entry_number,
                e.entry_date.isoformat(),
                e.label,
                e.account,
                e.direction.value,
                str(e.amount),
                e.currency.value,
                str(e.amount_eur_equiv),
                str(e.amount_fcfa_equiv),
                e.status.value,
                e.receipt_number or "",
                e.previous_hash,
                e.entry_hash,
            ]
        )

    buf.seek(0)
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="registre-batack.csv"'},
    )

