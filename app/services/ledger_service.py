import hashlib
import json
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PARITE_FCFA
from app.models.enums import Currency, LedgerDirection, LedgerStatus, UserRole
from app.models.finance import LedgerEntry
from app.models.user import User
from app.services.audit import record_audit_log

GENESIS_HASH = "0" * 64


def convert_currency(amount: Decimal, currency: Currency) -> tuple[Decimal, int]:
    """Convertit un montant exact EUR <-> FCFA selon la parité fixe (1 € = 655,957 FCFA)."""
    amt = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if currency == Currency.EUR:
        fcfa = int((amt * PARITE_FCFA).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return amt, fcfa
    # FCFA -> pas de centimes
    fcfa_int = int(Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    eur = (Decimal(fcfa_int) / PARITE_FCFA).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return eur, fcfa_int


def compute_entry_hash(
    *,
    entry_number: int,
    entry_date: date,
    label: str,
    account: str,
    direction: LedgerDirection,
    amount: Decimal,
    currency: Currency,
    previous_hash: str,
) -> str:
    """Calcule l'empreinte SHA-256 déterministe d'une écriture chaînée à la précédente."""
    payload = {
        "entry_number": entry_number,
        "entry_date": entry_date.isoformat(),
        "label": label,
        "account": account,
        "direction": direction.value,
        "amount": f"{Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}",
        "currency": currency.value,
        "previous_hash": previous_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def create_ledger_entry(
    db: AsyncSession,
    *,
    entry_date: date,
    label: str,
    account: str,
    direction: LedgerDirection,
    amount: Decimal,
    currency: Currency,
    initial_status: LedgerStatus = LedgerStatus.A_CONTRESIGNER,
    project_id: str | None = None,
    donation_id: str | None = None,
    justificatif_media_id: str | None = None,
    receipt_number: str | None = None,
    corrects_entry_id: str | None = None,
    actor: User | None = None,
    ip_address: str | None = None,
) -> LedgerEntry:
    """Crée une nouvelle écriture immuable chaînée à la dernière écriture du registre."""
    res = await db.execute(select(LedgerEntry).order_by(desc(LedgerEntry.entry_number)).limit(1))
    last = res.scalar_one_or_none()
    next_number = (last.entry_number + 1) if last else 1
    prev_hash = last.entry_hash if last else GENESIS_HASH

    eur_equiv, fcfa_equiv = convert_currency(amount, currency)
    amt_norm = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if currency == Currency.FCFA:
        amt_norm = Decimal(fcfa_equiv).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

    entry_hash = compute_entry_hash(
        entry_number=next_number,
        entry_date=entry_date,
        label=label,
        account=account,
        direction=direction,
        amount=amt_norm,
        currency=currency,
        previous_hash=prev_hash,
    )

    now = datetime.now(UTC)
    entry = LedgerEntry(
        entry_number=next_number,
        entry_date=entry_date,
        label=label,
        account=account,
        direction=direction,
        amount=amt_norm,
        currency=currency,
        amount_eur_equiv=eur_equiv,
        amount_fcfa_equiv=fcfa_equiv,
        status=initial_status,
        project_id=project_id,
        donation_id=donation_id,
        justificatif_media_id=justificatif_media_id,
        receipt_number=receipt_number,
        corrects_entry_id=corrects_entry_id,
        created_by_id=actor.id if actor else None,
        validated_by_id=actor.id if (actor and initial_status == LedgerStatus.VALIDEE) else None,
        validated_at=now if initial_status == LedgerStatus.VALIDEE else None,
        previous_hash=prev_hash,
        entry_hash=entry_hash,
    )
    db.add(entry)
    await db.flush()

    await record_audit_log(
        db,
        action="registry.entry_created",
        resource_type="ledger_entry",
        resource_id=entry.id,
        actor=actor,
        ip_address=ip_address,
        details={
            "entry_number": next_number,
            "direction": direction.value,
            "amount": str(amt_norm),
            "currency": currency.value,
            "status": initial_status.value,
        },
    )
    return entry


async def validate_ledger_entry(
    db: AsyncSession,
    *,
    entry: LedgerEntry,
    actor: User,
    ip_address: str | None = None,
) -> LedgerEntry:
    """Contresigne / valide une écriture en attente (séparation des tâches)."""
    if entry.status != LedgerStatus.A_CONTRESIGNER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cette écriture n'est plus en attente de contresignature.",
        )
    if entry.created_by_id and entry.created_by_id == actor.id and actor.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Vous ne pouvez pas contresigner votre propre écriture.",
        )

    entry.status = LedgerStatus.VALIDEE
    entry.validated_by_id = actor.id
    entry.validated_at = datetime.now(UTC)

    await record_audit_log(
        db,
        action="registry.entry_validated",
        resource_type="ledger_entry",
        resource_id=entry.id,
        actor=actor,
        ip_address=ip_address,
        details={"entry_number": entry.entry_number},
    )
    await db.commit()
    await db.refresh(entry)
    return entry


async def reject_ledger_entry(
    db: AsyncSession,
    *,
    entry: LedgerEntry,
    reason: str,
    actor: User,
    ip_address: str | None = None,
) -> LedgerEntry:
    """Rejette une écriture en attente avec un motif tracé."""
    if entry.status != LedgerStatus.A_CONTRESIGNER:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cette écriture n'est plus en attente de contresignature.",
        )
    entry.status = LedgerStatus.REJETEE
    entry.validated_by_id = actor.id
    entry.validated_at = datetime.now(UTC)
    entry.rejection_reason = reason

    await record_audit_log(
        db,
        action="registry.entry_rejected",
        resource_type="ledger_entry",
        resource_id=entry.id,
        actor=actor,
        ip_address=ip_address,
        details={"entry_number": entry.entry_number, "reason": reason},
    )
    await db.commit()
    await db.refresh(entry)
    return entry


async def verify_ledger_integrity(db: AsyncSession) -> dict:
    """Vérifie l'intégrité cryptographique de toute la chaîne d'écritures."""
    res = await db.execute(select(LedgerEntry).order_by(LedgerEntry.entry_number.asc()))
    entries = list(res.scalars().all())
    expected_prev = GENESIS_HASH
    expected_num = 1

    for entry in entries:
        if entry.entry_number != expected_num:
            return {
                "valid": False,
                "entries_checked": expected_num - 1,
                "broken_at_entry_number": entry.entry_number,
                "detail": f"Rupture de numérotation : attendu #{expected_num}, trouvé #{entry.entry_number}.",
            }
        if entry.previous_hash != expected_prev:
            return {
                "valid": False,
                "entries_checked": expected_num - 1,
                "broken_at_entry_number": entry.entry_number,
                "detail": f"Chaînage previous_hash rompu à l'écriture #{entry.entry_number}.",
            }
        recomputed = compute_entry_hash(
            entry_number=entry.entry_number,
            entry_date=entry.entry_date,
            label=entry.label,
            account=entry.account,
            direction=entry.direction,
            amount=entry.amount,
            currency=entry.currency,
            previous_hash=entry.previous_hash,
        )
        if entry.entry_hash != recomputed:
            return {
                "valid": False,
                "entries_checked": expected_num - 1,
                "broken_at_entry_number": entry.entry_number,
                "detail": f"Empreinte SHA-256 invalide sur l'écriture #{entry.entry_number}.",
            }
        expected_prev = entry.entry_hash
        expected_num += 1

    return {
        "valid": True,
        "entries_checked": len(entries),
        "broken_at_entry_number": None,
        "detail": "Chaîne d'écritures intègre." if entries else "Registre vide (intègre).",
    }


async def compute_registry_summary(db: AsyncSession) -> dict:
    """Calcule les soldes consolidés à partir des seules écritures VALIDÉES."""
    res = await db.execute(select(LedgerEntry))
    entries = list(res.scalars().all())

    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    by_account: dict[str, Decimal] = {}
    pending_count = 0
    validated_count = 0
    rejected_count = 0

    for e in entries:
        if e.status == LedgerStatus.A_CONTRESIGNER:
            pending_count += 1
            continue
        if e.status == LedgerStatus.REJETEE:
            rejected_count += 1
            continue
        validated_count += 1
        amt = Decimal(e.amount_eur_equiv)
        if e.account not in by_account:
            by_account[e.account] = Decimal("0.00")
        if e.direction == LedgerDirection.ENTREE:
            total_in += amt
            by_account[e.account] += amt
        else:
            total_out += amt
            by_account[e.account] -= amt

    balance_eur = (total_in - total_out).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    balance_fcfa = int((balance_eur * PARITE_FCFA).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    return {
        "total_validated_in_eur": total_in.quantize(Decimal("0.01")),
        "total_validated_out_eur": total_out.quantize(Decimal("0.01")),
        "available_balance_eur": balance_eur,
        "available_balance_fcfa": balance_fcfa,
        "pending_countersign_count": pending_count,
        "validated_count": validated_count,
        "rejected_count": rejected_count,
        "balances_by_account_eur": {k: v.quantize(Decimal("0.01")) for k, v in by_account.items()},
        "parity_fcfa_per_eur": PARITE_FCFA,
    }

