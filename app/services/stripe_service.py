from datetime import UTC, datetime, timedelta
from decimal import Decimal

import anyio
import stripe
from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.models.content import Project
from app.models.enums import (
    Currency,
    DonationFrequency,
    DonationStatus,
    LedgerDirection,
    LedgerStatus,
    PaymentMethod,
)
from app.models.finance import Donation, StripeEvent
from app.schemas.finance import DonationCheckoutRequest
from app.services.audit import record_audit_log
from app.services.ledger_service import create_ledger_entry, ledger_write_lock


def get_stripe_client():
    """
    Configure Stripe : une clé de production est refusée tant que
    PAIEMENT_REEL_AUTORISE != 'oui'.
    """
    settings = get_settings()
    key = settings.STRIPE_SECRET_KEY.strip()
    if not key:
        return None
    if not key.startswith("sk_test_") and settings.PAIEMENT_REEL_AUTORISE != "oui":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Clé Stripe de production refusée : mode test uniquement tant que PAIEMENT_REEL_AUTORISE != 'oui'.",
        )
    stripe.api_key = key
    return stripe


async def create_stripe_checkout_for_donation(
    db: AsyncSession, data: DonationCheckoutRequest
) -> tuple[Donation, str]:
    """
    Crée un don en base (PENDING avec durée de conservation RGPD) puis une session
    Stripe Checkout de façon non-bloquante, sans créer de client Stripe prématuré.
    """
    settings = get_settings()

    if data.frequence == DonationFrequency.MENSUEL and not settings.DON_MENSUEL_ACTIF:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le don mensuel n'est pas encore activé.",
        )
    if data.frequence == DonationFrequency.MENSUEL and data.moyen != "carte":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Le don mensuel se fait uniquement par carte bancaire.",
        )
    if data.montant < settings.DON_MONTANT_MIN_EUR or data.montant > settings.DON_MONTANT_MAX_EUR:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Le montant doit être compris entre {settings.DON_MONTANT_MIN_EUR} € et {settings.DON_MONTANT_MAX_EUR} €.",
        )

    project: Project | None = None
    if data.projet:
        res = await db.execute(select(Project).where(Project.slug == data.projet))
        project = res.scalar_one_or_none()
        if project is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Projet inconnu.")

    stripe_client = get_stripe_client()
    if stripe_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Le paiement en ligne n'est pas encore configuré.",
        )

    site_url = settings.PUBLIC_SITE_URL.rstrip("/")
    is_monthly = data.frequence == DonationFrequency.MENSUEL
    now = datetime.now(UTC)

    donation = Donation(
        project_id=project.id if project else None,
        amount_eur=Decimal(data.montant).quantize(Decimal("0.01")),
        original_amount=Decimal(data.montant).quantize(Decimal("0.01")),
        currency=Currency.EUR,
        frequency=data.frequence,
        payment_method=PaymentMethod.BANCONTACT if data.moyen == "bancontact" else PaymentMethod.CARTE,
        status=DonationStatus.PENDING,
        donor_first_name=data.prenom.strip(),
        donor_last_name=data.nom.strip(),
        donor_email=data.email.lower().strip(),
        tax_receipt_requested=data.attestation,
        terms_accepted=data.conditions,
        language=data.langue,
        retention_expires_at=now + timedelta(days=settings.PENDING_DONATION_RETENTION_DAYS),
    )
    db.add(donation)
    await db.flush()

    try:
        product_label = (
            f"{settings.STRIPE_PRODUCT_NAME} (mensuel)" if is_monthly else settings.STRIPE_PRODUCT_NAME
        )
        price_data: dict = {
            "currency": "eur",
            "unit_amount": int(data.montant) * 100,
            "product_data": {"name": product_label},
        }
        if is_monthly:
            price_data["recurring"] = {"interval": "month"}

        session = await anyio.to_thread.run_sync(
            lambda: stripe_client.checkout.Session.create(
                mode="subscription" if is_monthly else "payment",
                payment_method_types=["bancontact" if data.moyen == "bancontact" else "card"],
                line_items=[{"quantity": 1, "price_data": price_data}],
                customer_email=donation.donor_email,
                billing_address_collection="required" if data.attestation else "auto",
                locale=data.langue,
                metadata={
                    "donation_id": donation.id,
                    "projet": data.projet or "general",
                    "attestation": "oui" if data.attestation else "non",
                    "langue": data.langue,
                },
                success_url=f"{site_url}/{data.langue}/don/merci?session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{site_url}/{data.langue}/don?annule=1",
            )
        )
        donation.stripe_session_id = session.id
        await db.commit()
        await db.refresh(donation)
        return donation, session.url
    except stripe.error.StripeError as exc:
        await db.rollback()
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Erreur lors de l'initialisation du paiement Stripe ({exc.code or 'stripe'}).",
        ) from exc


async def _extract_stripe_fee_eur(stripe_client, payment_intent_id: str | None) -> Decimal:
    """Récupère les frais Stripe exacts en EUR si disponibles sur le PaymentIntent."""
    if not payment_intent_id:
        return Decimal("0.00")
    try:
        pi = await anyio.to_thread.run_sync(
            lambda: stripe_client.PaymentIntent.retrieve(
                payment_intent_id, expand=["latest_charge.balance_transaction"]
            )
        )
        charge = getattr(pi, "latest_charge", None) or (pi.get("latest_charge") if isinstance(pi, dict) else None)
        bt = getattr(charge, "balance_transaction", None) or (
            charge.get("balance_transaction") if isinstance(charge, dict) else None
        )
        fee_cents = getattr(bt, "fee", None) if bt is not None else None
        if fee_cents is None and isinstance(bt, dict):
            fee_cents = bt.get("fee")
        if isinstance(fee_cents, int) and fee_cents > 0:
            return (Decimal(fee_cents) / Decimal(100)).quantize(Decimal("0.01"))
    except Exception:
        return Decimal("0.00")
    return Decimal("0.00")


async def process_stripe_webhook(
    db: AsyncSession, *, payload: bytes, signature: str | None
) -> dict:
    """Vérifie la signature Stripe, garantit l'idempotence et enregistre dons, frais, remboursements et litiges."""
    settings = get_settings()
    secret = settings.STRIPE_WEBHOOK_SECRET.strip()
    stripe_client = get_stripe_client()
    if not secret or stripe_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook Stripe non configuré.",
        )

    try:
        event = await anyio.to_thread.run_sync(
            lambda: stripe_client.Webhook.construct_event(payload, signature, secret)
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature Stripe invalide.",
        ) from exc

    event_id = event["id"]
    event_type = event["type"]

    async with ledger_write_lock(db):
        existing = await db.execute(select(StripeEvent).where(StripeEvent.event_id == event_id))
        if existing.scalar_one_or_none() is not None:
            return {"received": True, "duplicate": True}

        db.add(StripeEvent(event_id=event_id, event_type=event_type))

        if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
            session_obj = event["data"]["object"]
            if session_obj.get("payment_status") == "paid":
                await _mark_checkout_paid(db, stripe_client, session_obj)
        elif event_type == "checkout.session.expired":
            session_obj = event["data"]["object"]
            res = await db.execute(
                select(Donation).where(Donation.stripe_session_id == session_obj.get("id"))
            )
            donation = res.scalar_one_or_none()
            if donation and donation.status == DonationStatus.PENDING:
                donation.status = DonationStatus.CANCELLED
        elif event_type == "invoice.paid":
            await _handle_invoice_paid(db, stripe_client, event["data"]["object"])
        elif event_type == "invoice.payment_failed":
            sub_id = event["data"]["object"].get("subscription")
            if sub_id:
                res = await db.execute(
                    select(Donation).where(Donation.stripe_subscription_id == sub_id)
                )
                donation = res.scalar_one_or_none()
                if donation:
                    donation.status = DonationStatus.FAILED
        elif event_type == "charge.refunded":
            await _handle_charge_refunded(db, event["data"]["object"])
        elif event_type == "charge.dispute.created":
            await _handle_dispute_created(db, event["data"]["object"])
        elif event_type == "customer.subscription.deleted":
            sub_id = event["data"]["object"].get("id")
            await record_audit_log(
                db,
                action="stripe.subscription_deleted",
                resource_type="donation",
                details={"subscription_id": sub_id},
            )

        await db.commit()
    return {"received": True, "event_type": event_type}


async def _mark_checkout_paid(db: AsyncSession, stripe_client, session_obj: dict) -> None:
    session_id = session_obj.get("id")
    metadata = session_obj.get("metadata") or {}
    donation_id = metadata.get("donation_id")

    donation: Donation | None = None
    if donation_id:
        res = await db.execute(select(Donation).where(Donation.id == donation_id))
        donation = res.scalar_one_or_none()
    if donation is None and session_id:
        res = await db.execute(select(Donation).where(Donation.stripe_session_id == session_id))
        donation = res.scalar_one_or_none()

    if donation is None or donation.status == DonationStatus.PAID:
        return

    now = datetime.now(UTC)
    donation.status = DonationStatus.PAID
    donation.paid_at = now
    donation.retention_expires_at = None
    donation.stripe_customer_id = session_obj.get("customer") or donation.stripe_customer_id
    donation.stripe_payment_intent_id = session_obj.get("payment_intent")
    donation.stripe_subscription_id = session_obj.get("subscription")

    await create_ledger_entry(
        db,
        entry_date=now.date(),
        label="Don en ligne (Stripe)",
        account="Fonds chez l'institution agréée",
        direction=LedgerDirection.ENTREE,
        amount=Decimal(donation.amount_eur),
        currency=Currency.EUR,
        initial_status=LedgerStatus.VALIDEE,
        project_id=donation.project_id,
        donation_id=donation.id,
        receipt_number=session_obj.get("payment_intent") or session_id,
    )

    fee_eur = await _extract_stripe_fee_eur(stripe_client, donation.stripe_payment_intent_id)
    if fee_eur > Decimal("0.00"):
        await create_ledger_entry(
            db,
            entry_date=now.date(),
            label="Frais de paiement Stripe",
            account="Fonds chez l'institution agréée",
            direction=LedgerDirection.SORTIE,
            amount=fee_eur,
            currency=Currency.EUR,
            initial_status=LedgerStatus.VALIDEE,
            project_id=donation.project_id,
            donation_id=donation.id,
            receipt_number=donation.stripe_payment_intent_id,
        )


async def _handle_invoice_paid(db: AsyncSession, stripe_client, invoice_obj: dict) -> None:
    sub_id = invoice_obj.get("subscription")
    amount_paid = invoice_obj.get("amount_paid") or 0
    if not sub_id or amount_paid <= 0:
        return

    # Éviter de dupliquer le tout premier paiement déjà enregistré via checkout.session.completed
    if invoice_obj.get("billing_reason") == "subscription_create":
        return

    res = await db.execute(
        select(Donation)
        .where(Donation.stripe_subscription_id == sub_id)
        .order_by(Donation.created_at.asc())
        .limit(1)
    )
    parent = res.scalar_one_or_none()
    if parent is None:
        return

    now = datetime.now(UTC)
    amount_eur = (Decimal(amount_paid) / Decimal(100)).quantize(Decimal("0.01"))
    pi_id = invoice_obj.get("payment_intent")

    renewal = Donation(
        project_id=parent.project_id,
        amount_eur=amount_eur,
        original_amount=amount_eur,
        currency=Currency.EUR,
        frequency=DonationFrequency.MENSUEL,
        payment_method=parent.payment_method,
        status=DonationStatus.PAID,
        donor_first_name=parent.donor_first_name,
        donor_last_name=parent.donor_last_name,
        donor_email=parent.donor_email,
        tax_receipt_requested=parent.tax_receipt_requested,
        terms_accepted=True,
        language=parent.language,
        stripe_customer_id=parent.stripe_customer_id,
        stripe_payment_intent_id=pi_id,
        stripe_subscription_id=sub_id,
        paid_at=now,
    )
    db.add(renewal)
    await db.flush()

    await create_ledger_entry(
        db,
        entry_date=now.date(),
        label="Échéance don mensuel (Stripe)",
        account="Fonds chez l'institution agréée",
        direction=LedgerDirection.ENTREE,
        amount=amount_eur,
        currency=Currency.EUR,
        initial_status=LedgerStatus.VALIDEE,
        project_id=parent.project_id,
        donation_id=renewal.id,
        receipt_number=invoice_obj.get("id"),
    )

    fee_eur = await _extract_stripe_fee_eur(stripe_client, pi_id)
    if fee_eur > Decimal("0.00"):
        await create_ledger_entry(
            db,
            entry_date=now.date(),
            label="Frais de paiement Stripe",
            account="Fonds chez l'institution agréée",
            direction=LedgerDirection.SORTIE,
            amount=fee_eur,
            currency=Currency.EUR,
            initial_status=LedgerStatus.VALIDEE,
            project_id=parent.project_id,
            donation_id=renewal.id,
            receipt_number=pi_id,
        )


async def _handle_charge_refunded(db: AsyncSession, charge_obj: dict) -> None:
    pi_id = charge_obj.get("payment_intent")
    if not pi_id:
        return
    res = await db.execute(select(Donation).where(Donation.stripe_payment_intent_id == pi_id))
    donation = res.scalar_one_or_none()
    if donation is None or donation.status == DonationStatus.REFUNDED:
        return

    refunded_cents = charge_obj.get("amount_refunded") or int(Decimal(donation.amount_eur) * 100)
    refund_eur = (Decimal(refunded_cents) / Decimal(100)).quantize(Decimal("0.01"))
    donation.status = DonationStatus.REFUNDED

    await create_ledger_entry(
        db,
        entry_date=datetime.now(UTC).date(),
        label="Remboursement don en ligne (Stripe)",
        account="Fonds chez l'institution agréée",
        direction=LedgerDirection.SORTIE,
        amount=refund_eur,
        currency=Currency.EUR,
        initial_status=LedgerStatus.VALIDEE,
        project_id=donation.project_id,
        donation_id=donation.id,
        receipt_number=pi_id,
    )


async def _handle_dispute_created(db: AsyncSession, dispute_obj: dict) -> None:
    pi_id = dispute_obj.get("payment_intent")
    amount_cents = dispute_obj.get("amount") or 0
    if not pi_id:
        return
    res = await db.execute(select(Donation).where(Donation.stripe_payment_intent_id == pi_id))
    donation = res.scalar_one_or_none()
    if donation is None:
        return

    donation.status = DonationStatus.DISPUTED
    dispute_eur = (
        (Decimal(amount_cents) / Decimal(100)).quantize(Decimal("0.01"))
        if amount_cents > 0
        else Decimal(donation.amount_eur)
    )
    await create_ledger_entry(
        db,
        entry_date=datetime.now(UTC).date(),
        label="Litige paiement en ligne (Stripe)",
        account="Fonds chez l'institution agréée",
        direction=LedgerDirection.SORTIE,
        amount=dispute_eur,
        currency=Currency.EUR,
        initial_status=LedgerStatus.VALIDEE,
        project_id=donation.project_id,
        donation_id=donation.id,
        receipt_number=dispute_obj.get("id"),
    )


async def cleanup_expired_pending_donations(db: AsyncSession) -> int:
    """Supprime les dons PENDING abandonnés au-delà de leur durée de conservation RGPD."""
    now = datetime.now(UTC)
    stmt = delete(Donation).where(
        Donation.status == DonationStatus.PENDING,
        Donation.retention_expires_at.is_not(None),
        Donation.retention_expires_at < now,
    )
    res = await db.execute(stmt)
    await db.commit()
    return res.rowcount or 0
