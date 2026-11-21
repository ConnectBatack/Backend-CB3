import asyncio
from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update

from app.models.enums import LedgerStatus
from app.models.finance import LedgerEntry
from tests.conftest import login_as


@pytest.mark.asyncio
async def test_ledger_immutable_chain_no_admin_bypass_and_tamper_detection(
    client: AsyncClient, staff_users, db_session
):
    admin_h = await login_as(client, "admin@batack.org", "AdminPass2026!")
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")
    enc_h = await login_as(client, "encadrant@batack.org", "EncadrantPass2026!")

    # Référence inconnue refusée (404)
    bad_ref = await client.post(
        "/api/v1/registry/entries",
        headers=tres_h,
        json={
            "entry_date": "2026-11-10",
            "label": "Test FK",
            "account": "Caisse",
            "direction": "entree",
            "amount": "10.00",
            "currency": "EUR",
            "project_id": "00000000-0000-0000-0000-000000000000",
        },
    )
    assert bad_ref.status_code == 404

    # L'admin crée une écriture et ne peut PAS la contresigner lui-même
    adm_entry = await client.post(
        "/api/v1/registry/entries",
        headers=admin_h,
        json={
            "entry_date": "2026-11-06",
            "label": "Apport caisse",
            "account": "Caisse",
            "direction": "entree",
            "amount": "50.00",
            "currency": "EUR",
        },
    )
    assert adm_entry.status_code == 201
    assert (
        await client.post(f"/api/v1/registry/entries/{adm_entry.json()['id']}/validate", headers=admin_h)
    ).status_code == 403

    # Un membre soumet une cotisation Mobile Money en FCFA
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
    assert entry1["amount_eur_equiv"] == "1000.00"

    # Le trésorier contresigne -> crée une nouvelle écriture chaînée VALIDATION
    val = await client.post(f"/api/v1/registry/entries/{entry1['id']}/validate", headers=tres_h)
    assert val.status_code == 200
    assert val.json()["status"] == "validee"

    sum_after = await client.get("/api/v1/registry/summary", headers=enc_h)
    assert sum_after.json()["available_balance_eur"] == "1000.00"

    # Intégrité intègre avant altération
    integ = await client.get("/api/v1/registry/verify-integrity", headers=enc_h)
    assert integ.json()["valid"] is True

    # Altération directe du statut sur la ligne d'origine -> immédiatement détectée par verify-integrity
    await db_session.execute(
        update(LedgerEntry).where(LedgerEntry.id == adm_entry.json()["id"]).values(status=LedgerStatus.VALIDEE)
    )
    await db_session.commit()
    integ_broken = await client.get("/api/v1/registry/verify-integrity", headers=enc_h)
    assert integ_broken.json()["valid"] is False
    assert integ_broken.json()["broken_at_entry_number"] == 1


@pytest.mark.asyncio
async def test_concurrent_ledger_entries_succeed(client: AsyncClient, staff_users):
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")

    async def post_one(i: int):
        return await client.post(
            "/api/v1/registry/entries",
            headers=tres_h,
            json={
                "entry_date": "2026-11-15",
                "label": f"Entrée simultanée {i}",
                "account": "Caisse",
                "direction": "entree",
                "amount": "10.00",
                "currency": "EUR",
            },
        )

    responses = await asyncio.gather(*(post_one(i) for i in range(5)))
    assert all(r.status_code == 201 for r in responses)
    numbers = sorted(r.json()["entry_number"] for r in responses)
    assert numbers == [1, 2, 3, 4, 5]


@pytest.mark.asyncio
async def test_transfers_create_paired_ledger_entries_and_repay(client: AsyncClient, staff_users, db_session):
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

    # Les soldes par compte reflètent -500 en Banque et +500 en Caisse
    summary = (await client.get("/api/v1/registry/summary", headers=enc_h)).json()
    assert summary["balances_by_account_eur"]["Banque du COVIDEBA"] == "-500.00"
    assert summary["balances_by_account_eur"]["Caisse"] == "500.00"

    assert (await client.post(f"/api/v1/transfers/{tid}/repay", headers=enc_h)).status_code == 403

    repaid = await client.post(f"/api/v1/transfers/{tid}/repay", headers=tres_h)
    assert repaid.status_code == 200
    assert repaid.json()["is_repaid"] is True

    summary_after = (await client.get("/api/v1/registry/summary", headers=enc_h)).json()
    assert Decimal(summary_after["balances_by_account_eur"]["Banque du COVIDEBA"]) == Decimal("0.00")
    assert Decimal(summary_after["balances_by_account_eur"]["Caisse"]) == Decimal("0.00")
    assert (await db_session.execute(select(LedgerEntry))).scalars().all().__len__() == 4
