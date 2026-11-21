import csv
import io
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, REGISTRY_ENTRY_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.content import Project
from app.models.enums import LedgerDirection, LedgerStatus, UserRole
from app.models.finance import LedgerEntry
from app.models.media import MediaAsset
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
    ledger_write_lock,
    reject_ledger_entry,
    resolve_effective_operations,
    validate_ledger_entry,
    verify_ledger_integrity,
)

router = APIRouter(prefix="/registry", tags=["Registre financier chaîné"])


def _resolved_to_out(item: dict) -> LedgerEntryOut:
    e: LedgerEntry = item["entry"]
    return LedgerEntryOut(
        id=e.id,
        entry_number=e.entry_number,
        entry_type=e.entry_type,
        entry_date=e.entry_date,
        label=e.label,
        account=e.account,
        direction=e.direction,
        amount=e.amount,
        currency=e.currency,
        amount_eur_equiv=e.amount_eur_equiv,
        amount_fcfa_equiv=e.amount_fcfa_equiv,
        status=item["effective_status"],
        project_id=e.project_id,
        donation_id=e.donation_id,
        transfer_id=e.transfer_id,
        justificatif_media_id=e.justificatif_media_id,
        receipt_number=e.receipt_number,
        corrects_entry_id=e.corrects_entry_id,
        target_entry_id=e.target_entry_id,
        created_by_id=e.created_by_id,
        validated_by_id=item["validated_by_id"],
        validated_at=item["validated_at"],
        rejection_reason=item["rejection_reason"],
        previous_hash=e.previous_hash,
        entry_hash=e.entry_hash,
        created_at=e.created_at,
    )


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
    res = await db.execute(select(LedgerEntry).order_by(LedgerEntry.entry_number.asc()))
    resolved = resolve_effective_operations(list(res.scalars().all()))

    filtered = []
    for item in reversed(resolved):
        e: LedgerEntry = item["entry"]
        if status_filter is not None and item["effective_status"] != status_filter:
            continue
        if direction is not None and e.direction != direction:
            continue
        if account and e.account != account:
            continue
        if project_id and e.project_id != project_id:
            continue
        filtered.append(item)

    total = len(filtered)
    page = filtered[offset : offset + limit]
    return PaginatedResponse(
        items=[_resolved_to_out(item) for item in page],
        total=total,
        limit=limit,
        offset=offset,
    )


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
    if current_user.role == UserRole.MEMBRE and payload.direction == LedgerDirection.SORTIE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seul le trésorier ou un administrateur peut enregistrer une dépense.",
        )

    if payload.project_id:
        p_res = await db.execute(select(Project.id).where(Project.id == payload.project_id))
        if p_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")
    if payload.justificatif_media_id:
        m_res = await db.execute(select(MediaAsset.id).where(MediaAsset.id == payload.justificatif_media_id))
        if m_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Justificatif média introuvable.")
    if payload.corrects_entry_id:
        c_res = await db.execute(select(LedgerEntry.id).where(LedgerEntry.id == payload.corrects_entry_id))
        if c_res.scalar_one_or_none() is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Écriture corrigée introuvable.")

    async with ledger_write_lock(db):
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
    return _resolved_to_out(
        {
            "entry": entry,
            "effective_status": entry.status,
            "validated_by_id": entry.validated_by_id,
            "validated_at": entry.validated_at,
            "rejection_reason": entry.rejection_reason,
        }
    )


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
    resolved = await validate_ledger_entry(db, entry=entry, actor=current_user, ip_address=client_ip(request))
    return _resolved_to_out(resolved)


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
    resolved = await reject_ledger_entry(
        db, entry=entry, reason=payload.reason, actor=current_user, ip_address=client_ip(request)
    )
    return _resolved_to_out(resolved)


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
    resolved = resolve_effective_operations(list(res.scalars().all()))

    if fmt == "json":
        return [_resolved_to_out(item).model_dump(mode="json") for item in resolved]

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
    for item in resolved:
        e: LedgerEntry = item["entry"]
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
                item["effective_status"].value,
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
