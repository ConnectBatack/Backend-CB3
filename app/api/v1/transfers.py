from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, TREASURY_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.content import Project
from app.models.enums import LedgerDirection, LedgerStatus
from app.models.finance import FundTransfer
from app.models.user import User
from app.schemas.finance import TransferCreate, TransferOut
from app.services.audit import record_audit_log
from app.services.ledger_service import create_ledger_entry, ledger_write_lock

router = APIRouter(prefix="/transfers", tags=["Transferts entre rubriques"])


@router.get("", response_model=list[TransferOut], summary="Lister les transferts internes")
async def list_transfers(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[TransferOut]:
    res = await db.execute(
        select(FundTransfer).order_by(FundTransfer.transfer_date.desc(), FundTransfer.created_at.desc())
    )
    return [TransferOut.model_validate(t) for t in res.scalars().all()]


@router.post("", response_model=TransferOut, status_code=status.HTTP_201_CREATED, summary="Enregistrer un transfert entre comptes/rubriques")
async def create_transfer(
    payload: TransferCreate,
    request: Request,
    current_user: User = Depends(require_roles(*TREASURY_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> TransferOut:
    if (
        payload.source_project_id == payload.destination_project_id
        and payload.source_account == payload.destination_account
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La source et la destination du transfert doivent être différentes.",
        )

    for pid in (payload.source_project_id, payload.destination_project_id):
        if pid:
            p_res = await db.execute(select(Project.id).where(Project.id == pid))
            if p_res.scalar_one_or_none() is None:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Projet introuvable.")

    transfer = FundTransfer(
        **payload.model_dump(),
        created_by_id=current_user.id,
    )
    db.add(transfer)
    await db.flush()

    ip = client_ip(request)
    async with ledger_write_lock(db):
        # Un transfert = une écriture de sortie sur la source + une écriture d'entrée sur la destination
        await create_ledger_entry(
            db,
            entry_date=payload.transfer_date,
            label=f"Transfert sortant vers {payload.destination_account} ({payload.reason[:80]})",
            account=payload.source_account,
            direction=LedgerDirection.SORTIE,
            amount=payload.amount,
            currency=payload.currency,
            initial_status=LedgerStatus.VALIDEE,
            project_id=payload.source_project_id,
            transfer_id=transfer.id,
            actor=current_user,
            ip_address=ip,
        )
        await create_ledger_entry(
            db,
            entry_date=payload.transfer_date,
            label=f"Transfert entrant depuis {payload.source_account} ({payload.reason[:80]})",
            account=payload.destination_account,
            direction=LedgerDirection.ENTREE,
            amount=payload.amount,
            currency=payload.currency,
            initial_status=LedgerStatus.VALIDEE,
            project_id=payload.destination_project_id,
            transfer_id=transfer.id,
            actor=current_user,
            ip_address=ip,
        )

        await record_audit_log(
            db,
            action="transfers.created",
            resource_type="fund_transfer",
            resource_id=transfer.id,
            actor=current_user,
            ip_address=ip,
            details={"amount": str(payload.amount), "currency": payload.currency.value},
        )
        await db.commit()
    await db.refresh(transfer)
    return TransferOut.model_validate(transfer)


@router.post("/{transfer_id}/repay", response_model=TransferOut, summary="Marquer un transfert comme remboursé")
async def mark_transfer_repaid(
    transfer_id: str,
    request: Request,
    current_user: User = Depends(require_roles(*TREASURY_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> TransferOut:
    res = await db.execute(select(FundTransfer).where(FundTransfer.id == transfer_id))
    transfer = res.scalar_one_or_none()
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transfert introuvable.")
    if transfer.is_repaid:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ce transfert est déjà remboursé.")

    now = datetime.now(UTC)
    transfer.is_repaid = True
    transfer.repaid_at = now
    ip = client_ip(request)
    async with ledger_write_lock(db):
        await create_ledger_entry(
            db,
            entry_date=now.date(),
            label=f"Remboursement transfert vers {transfer.source_account}",
            account=transfer.destination_account,
            direction=LedgerDirection.SORTIE,
            amount=transfer.amount,
            currency=transfer.currency,
            initial_status=LedgerStatus.VALIDEE,
            project_id=transfer.destination_project_id,
            transfer_id=transfer.id,
            actor=current_user,
            ip_address=ip,
        )
        await create_ledger_entry(
            db,
            entry_date=now.date(),
            label=f"Remboursement transfert depuis {transfer.destination_account}",
            account=transfer.source_account,
            direction=LedgerDirection.ENTREE,
            amount=transfer.amount,
            currency=transfer.currency,
            initial_status=LedgerStatus.VALIDEE,
            project_id=transfer.source_project_id,
            transfer_id=transfer.id,
            actor=current_user,
            ip_address=ip,
        )

        await record_audit_log(
            db,
            action="transfers.repaid",
            resource_type="fund_transfer",
            resource_id=transfer.id,
            actor=current_user,
            ip_address=ip,
        )
        await db.commit()
    await db.refresh(transfer)
    return TransferOut.model_validate(transfer)
