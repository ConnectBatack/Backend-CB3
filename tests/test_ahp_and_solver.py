import pytest
from httpx import AsyncClient

from tests.conftest import login_as


@pytest.mark.asyncio
async def test_ahp_consistency_and_task_precedence_cycle_rejected(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")

    t1 = await client.post(
        "/api/v1/tasks",
        headers=membre_h,
        json={"code_id": 1, "label": "Toiture", "cost_eur": "2500", "duration_months": 2, "weight": "1"},
    )
    t2 = await client.post(
        "/api/v1/tasks",
        headers=membre_h,
        json={
            "code_id": 2,
            "label": "Câblage",
            "cost_eur": "1200",
            "duration_months": 1,
            "weight": "1",
            "predecessor_code_ids": [1],
        },
    )
    assert t1.status_code == 201 and t2.status_code == 201

    # Créer un cycle 1 <- 2 <- 1 via PATCH doit être refusé
    cycle_patch = await client.patch(
        f"/api/v1/tasks/{t1.json()['id']}",
        headers=membre_h,
        json={"predecessor_code_ids": [2]},
    )
    assert cycle_patch.status_code == 400

    calc = await client.post(
        "/api/v1/ahp/calculate",
        headers=membre_h,
        json={
            "criteres": [
                {"id": 1, "nom": "Urgence sanitaire"},
                {"id": 2, "nom": "Impact village"},
                {"id": 3, "nom": "Coût"},
            ],
            "curseurs": {"1-2": 2, "1-3": 4, "2-3": 2},
            "notes": {"1-1": 10, "1-2": 9, "1-3": 6, "2-1": 7, "2-2": 8, "2-3": 8},
            "tache_ids": [1, 2],
        },
    )
    assert calc.status_code == 200
    res = calc.json()
    assert res["est_coherent"] is True

    saved = await client.post(
        "/api/v1/ahp/sessions",
        headers=membre_h,
        json={
            "name": "Pondération novembre",
            "criteres": [
                {"id": 1, "nom": "Urgence sanitaire"},
                {"id": 2, "nom": "Impact village"},
                {"id": 3, "nom": "Coût"},
            ],
            "curseurs": {"1-2": 2, "1-3": 4, "2-3": 2},
            "notes": {"1-1": 10, "1-2": 9, "1-3": 6, "2-1": 7, "2-2": 8, "2-3": 8},
            "tache_ids": [1, 2],
            "apply_immediately": True,
        },
    )
    assert saved.status_code == 201
    assert saved.json()["applied_to_tasks"] is True


@pytest.mark.asyncio
async def test_solver_milp_v5_5_and_reject_tampered_client_plan(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")

    instance = {
        "nom": "exemple_dispensaire",
        "h": 6,
        "S0": 3200,
        "Smin": 500,
        "delta": 0.1,
        "D": [900, 900, 900, 900, 900, 900],
        "taches": [
            {"id": 1, "libelle": "Toiture du dispensaire", "cout": 2500, "duree": 2, "poids": 9},
            {"id": 2, "libelle": "Câblage électrique", "cout": 1200, "duree": 1, "poids": 8},
            {"id": 3, "libelle": "Matériel d'analyse", "cout": 2000, "duree": 1, "poids": 7},
            {"id": 4, "libelle": "Entretien des batteries", "cout": 400, "duree": 1, "poids": 6},
            {"id": 5, "libelle": "Salaire d'un instituteur", "cout": 900, "duree": 3, "poids": 7},
            {"id": 6, "libelle": "Citerne d'eau", "cout": 1500, "duree": 2, "poids": 5},
        ],
        "precedences": [[1, 2], [2, 3]],
    }

    # Un résultat falsifié envoyé par le navigateur (solde négatif -4900) doit être refusé
    tampered = await client.post(
        "/api/v1/plans",
        headers=membre_h,
        json={
            "name": "Plan falsifié",
            "instance_data": instance,
            "solver_result": {
                "statut": "optimal",
                "valeur": 999,
                "lancements": [
                    {"id": 1, "mois": 1},
                    {"id": 2, "mois": 1},
                    {"id": 3, "mois": 1},
                    {"id": 4, "mois": 1},
                    {"id": 5, "mois": 1},
                    {"id": 6, "mois": 1},
                ],
                "soldes": [-4900, 0, 0, 0, 0, 0],
            },
        },
    )
    assert tampered.status_code == 400

    solved = await client.post("/api/v1/plans/solve", headers=membre_h, json=instance)
    assert solved.status_code == 200
    body = solved.json()
    assert body["statut"] == "optimal"
    assert all(s >= 500 for s in body["soldes"])

    saved = await client.post(
        "/api/v1/plans",
        headers=membre_h,
        json={"name": "Plan Novembre 2026", "instance_data": instance, "solver_result": body},
    )
    assert saved.status_code == 201
    plan_id = saved.json()["id"]

    validated = await client.post(f"/api/v1/plans/{plan_id}/validate", headers=membre_h)
    assert validated.status_code == 200
    assert validated.json()["status"] == "validated"
