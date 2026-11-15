import json
from types import SimpleNamespace

import pytest
from httpx import AsyncClient

from app.core.config import get_settings
from tests.conftest import login_as


@pytest.mark.asyncio
async def test_donation_validation_and_manual_recording(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")

    await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={
            "slug": "ecole",
            "title": {"fr": "École"},
            "target_amount_eur": "1200.00",
        },
    )

    # Validation stricte du don (montant < 5 EUR refusé)
    bad_don = await client.post(
        "/api/v1/donations/checkout",
        json={
            "montant": 2,
            "frequence": "ponctuel",
            "moyen": "carte",
            "projet": "ecole",
            "nom": "Dupont",
            "email": "donateur@example.org",
            "conditions": True,
            "langue": "fr",
        },
    )
    assert bad_don.status_code == 422

    # Un membre ne peut pas enregistrer de don manuel (réservé trésorier/admin)
    forb = await client.post(
        "/api/v1/donations/manual",
        headers=membre_h,
        json={
            "project_slug": "ecole",
            "amount": "100.00",
            "currency": "EUR",
            "payment_method": "especes",
            "donor_last_name": "Ngono",
            "donor_email": "ngono@example.org",
        },
    )
    assert forb.status_code == 403

    # Enregistrement manuel par le trésorier
    ok = await client.post(
        "/api/v1/donations/manual",
        headers=tres_h,
        json={
            "project_slug": "ecole",
            "amount": "100.00",
            "currency": "EUR",
            "payment_method": "especes",
            "donor_last_name": "Ngono",
            "donor_email": "ngono@example.org",
            "account": "Caisse",
        },
    )
    assert ok.status_code == 201
    assert ok.json()["status"] == "paid"

    proj = await client.get("/api/v1/projects/ecole")
    assert proj.json()["collected_amount_eur"] == "100.00"
    assert proj.json()["contributors_count"] == 1


@pytest.mark.asyncio
async def test_stripe_checkout_and_idempotent_webhook(client: AsyncClient, staff_users, monkeypatch):
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

    monkeypatch.setattr(stripe.Customer, "create", lambda **kwargs: SimpleNamespace(id="cus_test_1"))
    monkeypatch.setattr(
        stripe.checkout.Session,
        "create",
        lambda **kwargs: SimpleNamespace(id="cs_test_1", url="https://checkout.stripe.com/pay/cs_test_1"),
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

    fake_event = {
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
    monkeypatch.setattr(stripe.Webhook, "construct_event", lambda payload, sig, secret: fake_event)

    wh1 = await client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(fake_event),
        headers={"stripe-signature": "t=1,v1=sig"},
    )
    assert wh1.status_code == 200
    assert wh1.json()["received"] is True

    # Deuxième envoi du même événement -> idempotence
    wh2 = await client.post(
        "/api/v1/webhooks/stripe",
        content=json.dumps(fake_event),
        headers={"stripe-signature": "t=1,v1=sig"},
    )
    assert wh2.status_code == 200
    assert wh2.json()["duplicate"] is True

    proj = await client.get("/api/v1/projects/batteries")
    assert proj.json()["collected_amount_eur"] == "50.00"
    assert proj.json()["contributors_count"] == 1

    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_project_subscription_double_opt_in_and_unsubscribe(client: AsyncClient, staff_users):
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
    confirm_token = follow.json()["confirm_token"]
    unsub_token = follow.json()["unsubscribe_token"]

    confirmed = await client.get(f"/api/v1/subscriptions/confirm?token={confirm_token}")
    assert confirmed.status_code == 200
    assert confirmed.json()["status"] == "active"

    unsub = await client.get(f"/api/v1/subscriptions/unsubscribe?token={unsub_token}")
    assert unsub.status_code == 200
    assert unsub.json()["status"] == "unsubscribed"

