from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import update

from app.models.finance import LedgerEntry
from tests.conftest import login_as


@pytest.mark.asyncio
async def test_ledger_chain_countersign_parity_and_tamper_detection(client: AsyncClient, staff_users, db_session):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")
    enc_h = await login_as(client, "encadrant@batack.org", "EncadrantPass2026!")

    # Un membre ne peut pas enregistrer une dépense (sortie)
    mem_exp = await client.post(
        "/api/v1/registry/entries",
        headers=membre_h,
        json={
            "entry_date": "2026-11-10",
            "label": "Achat câbles",
            "account": "Caisse",
            "direction": "sortie",
            "amount": "45000",
            "currency": "FCFA",
        },
    )
    assert mem_exp.status_code == 403

    # Un membre peut soumettre une cotisation Mobile Money en attente de contresignature
    e1 = await client.post(
        "/api/v1/registry/entries",
        headers=membre_h,
        json={
            "entry_date": "2026-11-06",
            "label": "Cotisation Mobile Money",
            "account": "Mobile Money",
            "direction": "entree",
            "amount": "655957",
            "currency": "FCFA",
        },
    )
    assert e1.status_code == 201
    entry1 = e1.json()
    assert entry1["status"] == "a_contresigner"
    assert entry1["amount_eur_equiv"] == "1000.00"

    # Le membre ne peut pas auto-contresigner sa propre écriture
    self_val = await client.post(f"/api/v1/registry/entries/{entry1['id']}/validate", headers=membre_h)
    assert self_val.status_code == 403

    # Tant qu'elle n'est pas validée, elle ne compte pas dans le solde disponible
    sum_before = await client.get("/api/v1/registry/summary", headers=enc_h)
    assert sum_before.json()["available_balance_eur"] == "0.00"

    # Le trésorier contresigne l'écriture du membre
    val = await client.post(f"/api/v1/registry/entries/{entry1['id']}/validate", headers=tres_h)
    assert val.status_code == 200
    assert val.json()["status"] == "validee"

    sum_after = await client.get("/api/v1/registry/summary", headers=enc_h)
    assert sum_after.json()["available_balance_eur"] == "1000.00"

    # L'encadrant vérifie l'intégrité et exporte le registre
    integ = await client.get("/api/v1/registry/verify-integrity", headers=enc_h)
    assert integ.status_code == 200
    assert integ.json()["valid"] is True
    assert integ.json()["entries_checked"] == 1

    export_csv = await client.get("/api/v1/registry/export?fmt=csv", headers=enc_h)
    assert export_csv.status_code == 200
    assert "Cotisation Mobile Money" in export_csv.text

    # Simulation d'une altération directe en base -> l'intégrité doit échouer
    await db_session.execute(
        update(LedgerEntry).where(LedgerEntry.id == entry1["id"]).values(amount=Decimal("999999.00"))
    )
    await db_session.commit()
    integ_broken = await client.get("/api/v1/registry/verify-integrity", headers=enc_h)
    assert integ_broken.json()["valid"] is False
    assert integ_broken.json()["broken_at_entry_number"] == 1


@pytest.mark.asyncio
async def test_transfers_create_and_repay(client: AsyncClient, staff_users):
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")
    enc_h = await login_as(client, "encadrant@batack.org", "EncadrantPass2026!")

    tr = await client.post(
        "/api/v1/transfers",
        headers=tres_h,
        json={
            "source_account": "Banque du COVIDEBA",
            "destination_account": "Caisse",
            "amount": "500.00",
            "currency": "EUR",
            "reason": "Avance travaux toiture",
            "transfer_date": "2026-11-12",
        },
    )
    assert tr.status_code == 201
    tid = tr.json()["id"]

    # L'encadrant ne peut pas marquer remboursé
    forb = await client.post(f"/api/v1/transfers/{tid}/repay", headers=enc_h)
    assert forb.status_code == 403

    repaid = await client.post(f"/api/v1/transfers/{tid}/repay", headers=tres_h)
    assert repaid.status_code == 200
    assert repaid.json()["is_repaid"] is True

