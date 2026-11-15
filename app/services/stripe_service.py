from datetime import UTC, datetime
from decimal import Decimal

import stripe
from fastapi import HTTPException, status
from sqlalchemy import select
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
from app.services.ledger_service import create_ledger_entry


def get_stripe_client():
    """
    Configure Stripe en respectant la garde de sécurité de frontend/src/lib/stripe.js :
    une clé de production est refusée tant que PAIEMENT_REEL_AUTORISE != 'oui'.
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
    """Crée un don en base (PENDING) puis une session Stripe Checkout."""
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
    )
    db.add(donation)
    await db.flush()

    try:
        full_name = " ".join(p for p in [donation.donor_first_name, donation.donor_last_name] if p)
        customer = stripe_client.Customer.create(
            name=full_name,
            email=donation.donor_email,
            preferred_locales=[data.langue],
        )
        price_data: dict = {
            "currency": "eur",
            "unit_amount": int(data.montant) * 100,
            "product_data": {
                "name": "Don mensuel ConnectBatack 3" if is_monthly else "Don ConnectBatack 3"
            },
        }
        if is_monthly:
            price_data["recurring"] = {"interval": "month"}

        session = stripe_client.checkout.Session.create(
            mode="subscription" if is_monthly else "payment",
            payment_method_types=["bancontact" if data.moyen == "bancontact" else "card"],
            line_items=[{"quantity": 1, "price_data": price_data}],
            customer=customer.id,
            billing_address_collection="required" if data.attestation else "auto",
            locale=data.langue,
            # Aucune donnée personnelle dans les métadonnées Stripe
            metadata={
                "donation_id": donation.id,
                "projet": data.projet or "general",
                "attestation": "oui" if data.attestation else "non",
                "langue": data.langue,
            },
            success_url=f"{site_url}/{data.langue}/don/merci?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{site_url}/{data.langue}/don?annule=1",
        )
        donation.stripe_customer_id = customer.id
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


async def process_stripe_webhook(
    db: AsyncSession, *, payload: bytes, signature: str | None
) -> dict:
    """Vérifie la signature Stripe, garantit l'idempotence et enregistre l'écriture comptable."""
    settings = get_settings()
    secret = settings.STRIPE_WEBHOOK_SECRET.strip()
    stripe_client = get_stripe_client()
    if not secret or stripe_client is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook Stripe non configuré.",
        )

    try:
        event = stripe_client.Webhook.construct_event(payload, signature, secret)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Signature Stripe invalide.",
        ) from exc

    event_id = event["id"]
    event_type = event["type"]

    # Idempotence : ignorer si déjà traité
    existing = await db.execute(select(StripeEvent).where(StripeEvent.event_id == event_id))
    if existing.scalar_one_or_none() is not None:
        return {"received": True, "duplicate": True}

    db.add(StripeEvent(event_id=event_id, event_type=event_type))

    if event_type in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        session_obj = event["data"]["object"]
        if session_obj.get("payment_status") == "paid":
            await _mark_checkout_paid(db, session_obj)
    elif event_type == "checkout.session.expired":
        session_obj = event["data"]["object"]
        res = await db.execute(
            select(Donation).where(Donation.stripe_session_id == session_obj.get("id"))
        )
        donation = res.scalar_one_or_none()
        if donation and donation.status == DonationStatus.PENDING:
            donation.status = DonationStatus.CANCELLED
    elif event_type == "invoice.payment_failed":
        sub_id = event["data"]["object"].get("subscription")
        if sub_id:
            res = await db.execute(
                select(Donation).where(Donation.stripe_subscription_id == sub_id)
            )
            donation = res.scalar_one_or_none()
            if donation:
                donation.status = DonationStatus.FAILED

    await db.commit()
    return {"received": True, "event_type": event_type}


async def _mark_checkout_paid(db: AsyncSession, session_obj: dict) -> None:
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
    donation.stripe_payment_intent_id = session_obj.get("payment_intent")
    donation.stripe_subscription_id = session_obj.get("subscription")

    if donation.project_id:
        proj_res = await db.execute(select(Project).where(Project.id == donation.project_id))
        proj = proj_res.scalar_one_or_none()
        if proj is not None:
            proj.collected_amount_eur = Decimal(proj.collected_amount_eur) + Decimal(donation.amount_eur)
            proj.contributors_count += 1

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

