from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, TREASURY_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.finance import FundTransfer
from app.models.user import User
from app.schemas.finance import TransferCreate, TransferOut
from app.services.audit import record_audit_log

router = APIRouter(prefix="/transfers", tags=["Transferts entre rubriques"])


@router.get("", response_model=list[TransferOut], summary="Lister les transferts internes")
async def list_transfers(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[TransferOut]:
    res = await db.execute(select(FundTransfer).order_by(FundTransfer.transfer_date.desc(), FundTransfer.created_at.desc()))
    return [TransferOut.model_validate(t) for t in res.scalars().all()]


@router.post("", response_model=TransferOut, status_code=status.HTTP_201_CREATED, summary="Enregistrer un transfert entre rubriques")
async def create_transfer(
    payload: TransferCreate,
    request: Request,
    current_user: User = Depends(require_roles(*TREASURY_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> TransferOut:
    if (
        payload.source_project_id
        and payload.destination_project_id
        and payload.source_project_id == payload.destination_project_id
        and payload.source_account == payload.destination_account
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La source et la destination du transfert doivent être différentes.",
        )

    transfer = FundTransfer(
        **payload.model_dump(),
        created_by_id=current_user.id,
    )
    db.add(transfer)
    await db.flush()
    await record_audit_log(
        db,
        action="transfers.created",
        resource_type="fund_transfer",
        resource_id=transfer.id,
        actor=current_user,
        ip_address=client_ip(request),
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

    transfer.is_repaid = True
    transfer.repaid_at = datetime.now(UTC)
    await record_audit_log(
        db,
        action="transfers.repaid",
        resource_type="fund_transfer",
        resource_id=transfer.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(transfer)
    return TransferOut.model_validate(transfer)

