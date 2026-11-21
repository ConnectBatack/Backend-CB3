import json
import re
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from app.services.email_service import email_service
from tests.conftest import login_as


@pytest.mark.asyncio
async def test_manual_donation_without_email_anonymized_and_validated_before_public(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")

    p = await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={"slug": "ecole", "title": {"fr": "École"}, "target_amount_eur": "1200.00"},
    )
    assert p.status_code == 201

    # Don manuel en espèces SANS adresse e-mail (téléphone facultatif)
    ok = await client.post(
        "/api/v1/donations/manual",
        headers=tres_h,
        json={
            "project_slug": "ecole",
            "amount": "100.00",
            "currency": "EUR",
            "payment_method": "especes",
            "donor_last_name": "Kamga",
            "donor_phone": "+237699000000",
            "account": "Caisse",
        },
    )
    assert ok.status_code == 201
    assert ok.json()["donor_email"] is None
    assert ok.json()["donor_phone"] == "+237699000000"

    # Avant contresignature, le montant public collecté reste à 0.00 EUR
    proj_before = await client.get("/api/v1/projects/ecole")
    assert proj_before.json()["collected_amount_eur"] == "0.00"
    assert proj_before.json()["contributors_count"] == 0

    # Le nom du donateur n'apparaît ni dans le libellé du registre ni sur /public/transparence
    entries = (await client.get("/api/v1/registry/entries", headers=tres_h)).json()["items"]
    entry_id = entries[0]["id"]
    assert "Kamga" not in entries[0]["label"]

    # Contresignature par le membre -> le montant public passe à 100.00 EUR
    val = await client.post(f"/api/v1/registry/entries/{entry_id}/validate", headers=membre_h)
    assert val.status_code == 200

    proj_after = await client.get("/api/v1/projects/ecole")
    assert proj_after.json()["collected_amount_eur"] == "100.00"
    assert proj_after.json()["contributors_count"] == 1

    transp = (await client.get("/api/v1/public/transparence")).json()
    assert all("Kamga" not in e["label"] for e in transp["recent_entries"])

    # Un deuxième don manuel rejeté ne doit PAS être compté
    ok2 = await client.post(
        "/api/v1/donations/manual",
        headers=tres_h,
        json={
            "project_slug": "ecole",
            "amount": "1000.00",
            "currency": "EUR",
            "payment_method": "especes",
            "donor_last_name": "Autre",
            "account": "Caisse",
        },
    )
    assert ok2.status_code == 201
    entries2 = (await client.get("/api/v1/registry/entries", headers=tres_h)).json()["items"]
    rej = await client.post(
        f"/api/v1/registry/entries/{entries2[0]['id']}/reject",
        headers=membre_h,
        json={"reason": "Montant non remis"},
    )
    assert rej.status_code == 200
    proj_still = await client.get("/api/v1/projects/ecole")
    assert proj_still.json()["collected_amount_eur"] == "100.00"


@pytest.mark.asyncio
async def test_stripe_checkout_fees_refund_and_idempotency(client: AsyncClient, staff_users, monkeypatch):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={"slug": "batteries", "title": {"fr": "Batteries"}, "target_amount_eur": "3000.00"},
    )

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake_12345")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_12345")
    get_settings.cache_clear()

    import stripe

    # Vérifier qu'aucun Customer.create prématuré n'est appelé
    def _forbid_customer_create(**kwargs):
        raise AssertionError("Customer.create ne doit pas être appelé avant la finalisation du paiement")

    monkeypatch.setattr(stripe.Customer, "create", _forbid_customer_create)
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **kwargs: SimpleNamespace(id="cs_test_1", url="https://checkout.stripe.com/pay/cs_test_1"),
    )
    monkeypatch.setattr(
        stripe.PaymentIntent,
        "retrieve",
        lambda pi_id, expand=None: {"latest_charge": {"balance_transaction": {"fee": 175}}},
    )

    checkout = await client.post(
        "/api/v1/donations/checkout",
        json={
            "montant": 50,
            "frequence": "ponctuel",
            "moyen": "bancontact",
            "projet": "batteries",
            "prenom": "Alice",
            "nom": "Martin",
            "email": "alice@example.org",
            "attestation": True,
            "conditions": True,
            "langue": "fr",
        },
    )
    assert checkout.status_code == 200
    donation_id = checkout.json()["donation_id"]

    paid_event = {
        "id": "evt_test_001",
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "id": "cs_test_1",
                "payment_status": "paid",
                "payment_intent": "pi_test_1",
                "metadata": {"donation_id": donation_id},
            }
        },
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: paid_event)

    wh1 = await client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(paid_event),
        headers={"stripe-signature": "t=1,v1=sig"},
    )
    assert wh1.status_code == 200

    # Idempotence
    wh2 = await client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(paid_event),
        headers={"stripe-signature": "t=1,v1=sig"},
    )
    assert wh2.status_code == 200
    assert wh2.json()["duplicate"] is True

    # Frais Stripe (1.75 EUR) enregistrés en sortie dans le registre
    summary = (await client.get("/api/v1/registry/summary", headers=membre_h)).json()
    assert summary["total_validated_in_eur"] == "50.00"
    assert summary["total_validated_out_eur"] == "1.75"
    assert summary["available_balance_eur"] == "48.25"

    # Remboursement Stripe -> écriture de sortie compensatoire
    refund_event = {
        "id": "evt_refund_001",
        "type": "charge.refunded",
        "data": {"object": {"payment_intent": "pi_test_1", "amount_refunded": 5000}},
    }
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: refund_event)
    wh_ref = await client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(refund_event),
        headers={"stripe-signature": "t=1,v1=sig"},
    )
    assert wh_ref.status_code == 200
    don_after = (await client.get(f"/api/v1/donations/{donation_id}", headers=membre_h)).json()
    assert don_after["status"] == "refunded"

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_project_subscription_email_outbox_no_token_leak(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={"slug": "eau", "title": {"fr": "Eau potable"}, "target_amount_eur": "5000.00"},
    )

    follow = await client.post(
        "/api/v1/subscriptions/follow",
        json={
            "email": "suiveur@example.org",
            "projet": "eau",
            "langue": "fr",
            "consentement": True,
        },
    )
    assert follow.status_code == 201
    body = follow.json()
    assert "confirm_token" not in body
    assert "unsubscribe_token" not in body

    assert len(email_service.test_outbox) == 1
    mail_body = email_service.test_outbox[0].body
    confirm_tok = re.search(r"/subscriptions/confirm\?token=([^\s]+)", mail_body).group(1)
    unsub_tok = re.search(r"/subscriptions/unsubscribe\?token=([^\s]+)", mail_body).group(1)

    confirmed = await client.get(f"/api/v1/subscriptions/confirm?token={confirm_tok}")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "active"

    unsub = await client.get(f"/api/v1/subscriptions/unsubscribe?token={unsub_tok}")
    assert unsub.status_code == 200
    assert unsub.json()["status"] == "unsubscribed"
