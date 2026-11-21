import asyncio
import contextvars
import hashlib
import json
from contextlib import asynccontextmanager
from datetime import UTC, date, datetime
from decimal import ROUND_HALF_UP, Decimal

from fastapi import HTTPException, status
from sqlalchemy import desc, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PARITE_FCFA
from app.models.enums import (
    Currency,
    LedgerDirection,
    LedgerEntryType,
    LedgerStatus,
)
from app.models.finance import LedgerEntry
from app.models.user import User
from app.services.audit import record_audit_log

GENESIS_HASH = "0" * 64
_LEDGER_PROCESS_LOCK = asyncio.Lock()
_LOCK_HELD: contextvars.ContextVar[bool] = contextvars.ContextVar("ledger_lock_held", default=False)
_PG_ADVISORY_LEDGER_KEY = 7283910


@asynccontextmanager
async def ledger_write_lock(db: AsyncSession):
    """Sérialise les écritures du registre jusqu'au commit (verrou asyncio + verrou PostgreSQL)."""
    if _LOCK_HELD.get():
        yield
        return
    async with _LEDGER_PROCESS_LOCK:
        token = _LOCK_HELD.set(True)
        try:
            await _acquire_ledger_lock(db)
            yield
        finally:
            _LOCK_HELD.reset(token)


def convert_currency(amount: Decimal, currency: Currency) -> tuple[Decimal, int]:
    """Convertit un montant exact EUR <-> FCFA selon la parité fixe (1 € = 655,957 FCFA)."""
    amt = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if currency == Currency.EUR:
        fcfa = int((amt * PARITE_FCFA).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
        return amt, fcfa
    fcfa_int = int(Decimal(amount).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    eur = (Decimal(fcfa_int) / PARITE_FCFA).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return eur, fcfa_int


def compute_entry_hash(
    *,
    entry_number: int,
    entry_type: LedgerEntryType,
    entry_date: date,
    label: str,
    account: str,
    direction: LedgerDirection,
    amount: Decimal,
    currency: Currency,
    initial_status: LedgerStatus,
    project_id: str | None,
    donation_id: str | None,
    transfer_id: str | None,
    justificatif_media_id: str | None,
    receipt_number: str | None,
    corrects_entry_id: str | None,
    target_entry_id: str | None,
    created_by_id: str | None,
    rejection_reason: str | None,
    previous_hash: str,
) -> str:
    """Calcule l'empreinte SHA-256 déterministe couvrant la totalité de l'écriture."""
    payload = {
        "entry_number": entry_number,
        "entry_type": entry_type.value,
        "entry_date": entry_date.isoformat(),
        "label": label,
        "account": account,
        "direction": direction.value,
        "amount": f"{Decimal(amount).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)}",
        "currency": currency.value,
        "status": initial_status.value,
        "project_id": project_id,
        "donation_id": donation_id,
        "transfer_id": transfer_id,
        "justificatif_media_id": justificatif_media_id,
        "receipt_number": receipt_number,
        "corrects_entry_id": corrects_entry_id,
        "target_entry_id": target_entry_id,
        "created_by_id": created_by_id,
        "rejection_reason": rejection_reason,
        "previous_hash": previous_hash,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


async def _acquire_ledger_lock(db: AsyncSession) -> None:
    bind = db.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await db.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _PG_ADVISORY_LEDGER_KEY})


async def _append_ledger_row(
    db: AsyncSession,
    *,
    entry_type: LedgerEntryType,
    entry_date: date,
    label: str,
    account: str,
    direction: LedgerDirection,
    amount: Decimal,
    currency: Currency,
    initial_status: LedgerStatus,
    project_id: str | None = None,
    donation_id: str | None = None,
    transfer_id: str | None = None,
    justificatif_media_id: str | None = None,
    receipt_number: str | None = None,
    corrects_entry_id: str | None = None,
    target_entry_id: str | None = None,
    rejection_reason: str | None = None,
    actor: User | None = None,
) -> LedgerEntry:
    """Insère une ligne immuable dans le registre sous verrou transactionnel."""
    async with ledger_write_lock(db):
        res = await db.execute(select(LedgerEntry).order_by(desc(LedgerEntry.entry_number)).limit(1))
        last = res.scalar_one_or_none()
        next_number = (last.entry_number + 1) if last else 1
        prev_hash = last.entry_hash if last else GENESIS_HASH

        eur_equiv, fcfa_equiv = convert_currency(amount, currency)
        amt_norm = Decimal(amount).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        if currency == Currency.FCFA and amt_norm > Decimal("0.00"):
            amt_norm = Decimal(fcfa_equiv).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)

        actor_id = actor.id if actor else None
        entry_hash = compute_entry_hash(
            entry_number=next_number,
            entry_type=entry_type,
            entry_date=entry_date,
            label=label,
            account=account,
            direction=direction,
            amount=amt_norm,
            currency=currency,
            initial_status=initial_status,
            project_id=project_id,
            donation_id=donation_id,
            transfer_id=transfer_id,
            justificatif_media_id=justificatif_media_id,
            receipt_number=receipt_number,
            corrects_entry_id=corrects_entry_id,
            target_entry_id=target_entry_id,
            created_by_id=actor_id,
            rejection_reason=rejection_reason,
            previous_hash=prev_hash,
        )

        now = datetime.now(UTC)
        entry = LedgerEntry(
            entry_number=next_number,
            entry_type=entry_type,
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
            transfer_id=transfer_id,
            justificatif_media_id=justificatif_media_id,
            receipt_number=receipt_number,
            corrects_entry_id=corrects_entry_id,
            target_entry_id=target_entry_id,
            created_by_id=actor_id,
            validated_by_id=actor_id if initial_status == LedgerStatus.VALIDEE else None,
            validated_at=now if initial_status == LedgerStatus.VALIDEE else None,
            rejection_reason=rejection_reason,
            previous_hash=prev_hash,
            entry_hash=entry_hash,
        )
        db.add(entry)
        await db.flush()
        return entry


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
    transfer_id: str | None = None,
    justificatif_media_id: str | None = None,
    receipt_number: str | None = None,
    corrects_entry_id: str | None = None,
    actor: User | None = None,
    ip_address: str | None = None,
) -> LedgerEntry:
    """Crée une nouvelle écriture comptable immuable dans le registre."""
    entry = await _append_ledger_row(
        db,
        entry_type=LedgerEntryType.OPERATION,
        entry_date=entry_date,
        label=label,
        account=account,
        direction=direction,
        amount=amount,
        currency=currency,
        initial_status=initial_status,
        project_id=project_id,
        donation_id=donation_id,
        transfer_id=transfer_id,
        justificatif_media_id=justificatif_media_id,
        receipt_number=receipt_number,
        corrects_entry_id=corrects_entry_id,
        actor=actor,
    )

    await record_audit_log(
        db,
        action="registry.entry_created",
        resource_type="ledger_entry",
        resource_id=entry.id,
        actor=actor,
        ip_address=ip_address,
        details={
            "entry_number": entry.entry_number,
            "direction": direction.value,
            "amount": str(entry.amount),
            "currency": currency.value,
            "status": initial_status.value,
        },
    )
    return entry


def resolve_effective_operations(all_entries: list[LedgerEntry]) -> list[dict]:
    """
    Reconstruit l'état effectif de chaque écriture OPERATION sans jamais modifier la ligne d'origine :
    l'état est déduit de l'écriture initiale et des écritures VALIDATION/REJET chaînées.
    """
    transitions: dict[str, LedgerEntry] = {}
    operations: list[LedgerEntry] = []

    for e in sorted(all_entries, key=lambda x: x.entry_number):
        if e.entry_type == LedgerEntryType.OPERATION:
            operations.append(e)
        elif e.target_entry_id and e.target_entry_id not in transitions:
            transitions[e.target_entry_id] = e

    resolved: list[dict] = []
    for op in operations:
        tr = transitions.get(op.id)
        if tr is None:
            eff_status = op.status
            val_by = op.validated_by_id
            val_at = op.validated_at
            rej_reason = op.rejection_reason
        elif tr.entry_type == LedgerEntryType.VALIDATION:
            eff_status = LedgerStatus.VALIDEE
            val_by = tr.created_by_id
            val_at = tr.created_at
            rej_reason = None
        else:
            eff_status = LedgerStatus.REJETEE
            val_by = tr.created_by_id
            val_at = tr.created_at
            rej_reason = tr.rejection_reason

        resolved.append(
            {
                "entry": op,
                "effective_status": eff_status,
                "validated_by_id": val_by,
                "validated_at": val_at,
                "rejection_reason": rej_reason,
            }
        )
    return resolved


async def _get_effective_status_for_target(db: AsyncSession, entry: LedgerEntry) -> LedgerStatus:
    if entry.entry_type != LedgerEntryType.OPERATION:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Seule une écriture d'opération peut être contresignée ou rejetée.",
        )
    res = await db.execute(
        select(LedgerEntry).where(LedgerEntry.target_entry_id == entry.id)
    )
    tr = res.scalars().first()
    if tr is None:
        return entry.status
    return LedgerStatus.VALIDEE if tr.entry_type == LedgerEntryType.VALIDATION else LedgerStatus.REJETEE


async def validate_ledger_entry(
    db: AsyncSession,
    *,
    entry: LedgerEntry,
    actor: User,
    ip_address: str | None = None,
) -> dict:
    """Contresigne une écriture en ajoutant une nouvelle écriture chaînée (immutabilité stricte)."""
    async with ledger_write_lock(db):
        current_status = await _get_effective_status_for_target(db, entry)
        if current_status != LedgerStatus.A_CONTRESIGNER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cette écriture n'est plus en attente de contresignature.",
            )
        # Séparation stricte des tâches pour tout le monde, y compris l'administrateur
        if entry.created_by_id and entry.created_by_id == actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous ne pouvez pas contresigner votre propre écriture.",
            )

        tr = await _append_ledger_row(
            db,
            entry_type=LedgerEntryType.VALIDATION,
            entry_date=datetime.now(UTC).date(),
            label=f"Contresignature écriture #{entry.entry_number}",
            account=entry.account,
            direction=entry.direction,
            amount=Decimal("0.00"),
            currency=entry.currency,
            initial_status=LedgerStatus.VALIDEE,
            project_id=entry.project_id,
            target_entry_id=entry.id,
            actor=actor,
        )

        await record_audit_log(
            db,
            action="registry.entry_validated",
            resource_type="ledger_entry",
            resource_id=entry.id,
            actor=actor,
            ip_address=ip_address,
            details={"entry_number": entry.entry_number, "validation_entry_number": tr.entry_number},
        )
        await db.commit()
    return {
        "entry": entry,
        "effective_status": LedgerStatus.VALIDEE,
        "validated_by_id": actor.id,
        "validated_at": tr.created_at,
        "rejection_reason": None,
    }


async def reject_ledger_entry(
    db: AsyncSession,
    *,
    entry: LedgerEntry,
    reason: str,
    actor: User,
    ip_address: str | None = None,
) -> dict:
    """Rejette une écriture en ajoutant une nouvelle écriture chaînée avec motif."""
    async with ledger_write_lock(db):
        current_status = await _get_effective_status_for_target(db, entry)
        if current_status != LedgerStatus.A_CONTRESIGNER:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cette écriture n'est plus en attente de contresignature.",
            )
        if entry.created_by_id and entry.created_by_id == actor.id:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous ne pouvez pas statuer sur votre propre écriture.",
            )

        tr = await _append_ledger_row(
            db,
            entry_type=LedgerEntryType.REJET,
            entry_date=datetime.now(UTC).date(),
            label=f"Rejet écriture #{entry.entry_number}",
            account=entry.account,
            direction=entry.direction,
            amount=Decimal("0.00"),
            currency=entry.currency,
            initial_status=LedgerStatus.REJETEE,
            project_id=entry.project_id,
            target_entry_id=entry.id,
            rejection_reason=reason,
            actor=actor,
        )

        await record_audit_log(
            db,
            action="registry.entry_rejected",
            resource_type="ledger_entry",
            resource_id=entry.id,
            actor=actor,
            ip_address=ip_address,
            details={
                "entry_number": entry.entry_number,
                "rejection_entry_number": tr.entry_number,
                "reason": reason,
            },
        )
        await db.commit()
    return {
        "entry": entry,
        "effective_status": LedgerStatus.REJETEE,
        "validated_by_id": actor.id,
        "validated_at": tr.created_at,
        "rejection_reason": reason,
    }


async def verify_ledger_integrity(db: AsyncSession) -> dict:
    """Vérifie l'intégrité cryptographique de toute la chaîne (opérations + validations + rejets)."""
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
            entry_type=entry.entry_type,
            entry_date=entry.entry_date,
            label=entry.label,
            account=entry.account,
            direction=entry.direction,
            amount=entry.amount,
            currency=entry.currency,
            initial_status=entry.status,
            project_id=entry.project_id,
            donation_id=entry.donation_id,
            transfer_id=entry.transfer_id,
            justificatif_media_id=entry.justificatif_media_id,
            receipt_number=entry.receipt_number,
            corrects_entry_id=entry.corrects_entry_id,
            target_entry_id=entry.target_entry_id,
            created_by_id=entry.created_by_id,
            rejection_reason=entry.rejection_reason,
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
    """Calcule les soldes consolidés à partir des seules opérations EFFECTIVEMENT VALIDÉES."""
    res = await db.execute(select(LedgerEntry).order_by(LedgerEntry.entry_number.asc()))
    all_entries = list(res.scalars().all())
    ops = resolve_effective_operations(all_entries)

    total_in = Decimal("0.00")
    total_out = Decimal("0.00")
    by_account: dict[str, Decimal] = {}
    pending_count = 0
    validated_count = 0
    rejected_count = 0

    for item in ops:
        e: LedgerEntry = item["entry"]
        st: LedgerStatus = item["effective_status"]
        if st == LedgerStatus.A_CONTRESIGNER:
            pending_count += 1
            continue
        if st == LedgerStatus.REJETEE:
            rejected_count += 1
            continue
        validated_count += 1
        amt = Decimal(e.amount_eur_equiv)
        if e.account not in by_account:
            by_account[e.account] = Decimal("0.00")
        if e.direction == LedgerDirection.ENTREE:
            # Les transferts internes affectent les soldes par compte mais pas la collecte globale
            if e.transfer_id is None:
                total_in += amt
            by_account[e.account] += amt
        else:
            if e.transfer_id is None:
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


async def compute_project_ledger_stats(db: AsyncSession) -> dict[str, tuple[Decimal, int]]:
    """
    Calcule pour chaque projet le montant collecté et le nombre de contributeurs
    strictement à partir des écritures d'entrée VALIDÉES (hors transferts internes).
    """
    res = await db.execute(select(LedgerEntry).order_by(LedgerEntry.entry_number.asc()))
    ops = resolve_effective_operations(list(res.scalars().all()))

    collected: dict[str, Decimal] = {}
    contributors: dict[str, int] = {}

    for item in ops:
        e: LedgerEntry = item["entry"]
        if item["effective_status"] != LedgerStatus.VALIDEE:
            continue
        if e.project_id is None or e.direction != LedgerDirection.ENTREE or e.transfer_id is not None:
            continue
        collected[e.project_id] = collected.get(e.project_id, Decimal("0.00")) + Decimal(e.amount_eur_equiv)
        contributors[e.project_id] = contributors.get(e.project_id, 0) + 1

    return {
        pid: (collected[pid].quantize(Decimal("0.01")), contributors[pid])
        for pid in collected
    }
