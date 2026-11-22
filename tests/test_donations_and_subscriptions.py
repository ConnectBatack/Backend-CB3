import re

import pytest
from httpx import AsyncClient

from app.services.email_service import email_service
from tests.conftest import login_as


@pytest.mark.asyncio
async def test_payment_accounts_and_receipt_submission_flow(client: AsyncClient, staff_users):
    admin_h = await login_as(client, "admin@batack.org", "AdminPass2026!")
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")

    await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={"slug": "dispensaire", "title": {"fr": "Dispensaire"}, "target_amount_eur": "12000.00"},
    )

    # Configuration préalable depuis l'interface admin
    await client.put(
        "/api/v1/settings/organization",
        headers=admin_h,
        json={
            "covideba_email": "tresorerie@covideba.org",
            "covideba_phone": "+237 699 11 22 33",
            "bank_holder": "COVIDEBA Batack",
            "bank_name": "Afriland First Bank",
            "bank_account": "CM21 10005 00001 11223344556 77",
            "mobile_money_mtn": "+237 670 11 22 33",
            "mobile_money_orange": "+237 690 11 22 33",
        },
    )

    # Lecture des comptes de dépôt du COVIDEBA
    acc = await client.get("/api/v1/donations/accounts")
    assert acc.status_code == 200
    assert acc.json()["covideba_email"] == "tresorerie@covideba.org"
    assert acc.json()["bank_account"] == "CM21 10005 00001 11223344556 77"

    # Soumission d'un reçu de dépôt par un donateur (sans compte)
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    sub = await client.post(
        "/api/v1/donations/submit-receipt",
        data={
            "montant_fcfa": "25000",
            "moyen": "virement",
            "receipt_reference": "VIR-2026-8841",
            "nom": "Dupont",
            "prenom": "Jean",
            "email": "jean@example.org",
            "pays": "BE",
            "attestation": "true",
            "conditions": "true",
            "langue": "fr",
            "projet": "dispensaire",
        },
        files={"receipt_file": ("recu-virement.pdf", pdf_bytes, "application/pdf")},
    )
    assert sub.status_code == 201, sub.text
    body = sub.json()
    assert body["status"] == "pending"
    assert body["amount_fcfa"] == 25000
    assert body["receipt_reference"] == "VIR-2026-8841"

    # Le justificatif soumis est privé par défaut
    assert (await client.get(f"/api/v1/media/files/{body['receipt_media_id']}")).status_code == 401
    assert (
        await client.get(f"/api/v1/media/files/{body['receipt_media_id']}", headers=tres_h)
    ).status_code == 200

    # Avant validation du trésorier, le don reste PENDING et le montant public du projet est à 0.00
    don_before = await client.get(f"/api/v1/donations/{body['donation_id']}", headers=tres_h)
    assert don_before.json()["status"] == "pending"
    proj_before = await client.get("/api/v1/projects/dispensaire")
    assert proj_before.json()["collected_amount_eur"] == "0.00"

    # Le nom du donateur n'apparaît pas dans le libellé du registre
    entries = (await client.get("/api/v1/registry/entries", headers=tres_h)).json()["items"]
    assert "Dupont" not in entries[0]["label"]

    # Validation par le trésorier -> passe le don à PAID et met à jour les montants publics
    val = await client.post(f"/api/v1/registry/entries/{entries[0]['id']}/validate", headers=tres_h)
    assert val.status_code == 200

    don_after = await client.get(f"/api/v1/donations/{body['donation_id']}", headers=tres_h)
    assert don_after.json()["status"] == "paid"
    proj_after = await client.get("/api/v1/projects/dispensaire")
    assert float(proj_after.json()["collected_amount_eur"]) > 0
    assert proj_after.json()["contributors_count"] == 1


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
    assert ok.json()["status"] == "pending"

    proj_before = await client.get("/api/v1/projects/ecole")
    assert proj_before.json()["collected_amount_eur"] == "0.00"

    entries = (await client.get("/api/v1/registry/entries", headers=tres_h)).json()["items"]
    entry_id = entries[0]["id"]
    assert "Kamga" not in entries[0]["label"]

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
