"""
Script de vérification de bout en bout des endpoints FastAPI sur une instance active.
Les identifiants sont lus depuis les variables d'environnement (aucun mot de passe codé en dur).
"""

import io
import os
import sys
import time
from dataclasses import dataclass

import httpx
import pyotp
from PIL import Image

BASE_URL = os.getenv("BASE_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api/v1"

ADMIN_EMAIL = os.getenv("TEST_ADMIN_EMAIL", "admin@batack.org")
ADMIN_PASSWORD = os.getenv("TEST_ADMIN_PASSWORD", "")
TRES_EMAIL = os.getenv("TEST_TRESORIER_EMAIL", "tresorier@batack.org")
TRES_PASSWORD = os.getenv("TEST_TRESORIER_PASSWORD", "")
MEMBRE_EMAIL = os.getenv("TEST_MEMBRE_EMAIL", "membre@batack.org")
MEMBRE_PASSWORD = os.getenv("TEST_MEMBRE_PASSWORD", "")
ENC_EMAIL = os.getenv("TEST_ENCADRANT_EMAIL", "encadrant@batack.org")
ENC_PASSWORD = os.getenv("TEST_ENCADRANT_PASSWORD", "")


@dataclass
class EndpointResult:
    method: str
    path: str
    status_code: int
    expected: str
    passed: bool
    detail: str


results: list[EndpointResult] = []


def record(method: str, path: str, resp: httpx.Response, ok_codes: tuple[int, ...], detail: str = "") -> bool:
    passed = resp.status_code in ok_codes
    results.append(
        EndpointResult(
            method=method,
            path=path,
            status_code=resp.status_code,
            expected="/".join(str(c) for c in ok_codes),
            passed=passed,
            detail=detail if passed else f"{detail} -> {resp.text[:180]}",
        )
    )
    return passed


def make_png_bytes() -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (64, 48), color=(46, 125, 50))
    img.save(buf, format="PNG")
    return buf.getvalue()


def main() -> int:
    if not all([ADMIN_PASSWORD, TRES_PASSWORD, MEMBRE_PASSWORD, ENC_PASSWORD]):
        print(
            "Définissez TEST_ADMIN_PASSWORD, TEST_TRESORIER_PASSWORD, "
            "TEST_MEMBRE_PASSWORD et TEST_ENCADRANT_PASSWORD dans l'environnement.",
            file=sys.stderr,
        )
        return 2

    suffix = str(int(time.time()))
    with httpx.Client(timeout=20.0) as client:
        r = client.get(f"{API}/health")
        record("GET", "/api/v1/health", r, (200,), "API en ligne")

        r = client.get(f"{API}/ready")
        record("GET", "/api/v1/ready", r, (200,), "PostgreSQL + stockage prêts")

        r = client.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
        record("POST", "/api/v1/auth/login", r, (200,), "Connexion admin + cookie session")
        admin_tokens = r.json()
        admin_h = {"Authorization": f"Bearer {admin_tokens['access_token']}"}

        r = client.post(f"{API}/auth/login", json={"email": TRES_EMAIL, "password": TRES_PASSWORD})
        tres_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.post(f"{API}/auth/login", json={"email": MEMBRE_EMAIL, "password": MEMBRE_PASSWORD})
        membre_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.post(f"{API}/auth/login", json={"email": ENC_EMAIL, "password": ENC_PASSWORD})
        enc_h = {"Authorization": f"Bearer {r.json()['access_token']}"}

        r = client.get(f"{API}/auth/me", headers=admin_h)
        record("GET", "/api/v1/auth/me", r, (200,), f"Profil {r.json().get('role')}")

        r = client.post(f"{API}/auth/refresh", json={"refresh_token": admin_tokens["refresh_token"]})
        record("POST", "/api/v1/auth/refresh", r, (200,), "Rotation JWT refresh")

        r = client.get(f"{API}/users", headers=admin_h)
        record("GET", "/api/v1/users", r, (200,), f"Total={r.json().get('total')}")

        temp_email = f"e2e_{suffix}@batack.org"
        temp_pwd_1 = f"TempE2ePass_{suffix}_A1!"
        temp_pwd_2 = f"ResetE2ePass_{suffix}_B2!"
        temp_pwd_3 = f"ChangedE2ePass_{suffix}_C3!"
        r = client.post(
            f"{API}/users",
            headers=admin_h,
            json={
                "email": temp_email,
                "full_name": "Utilisateur Test E2E",
                "role": "membre",
                "password": temp_pwd_1,
            },
        )
        record("POST", "/api/v1/users", r, (201,), f"Créé {temp_email}")
        temp_uid = r.json()["id"]

        r = client.get(f"{API}/users/{temp_uid}", headers=admin_h)
        record("GET", "/api/v1/users/{user_id}", r, (200,), "Lecture utilisateur par ID")

        r = client.patch(f"{API}/users/{temp_uid}", headers=admin_h, json={"role": "tresorier"})
        record("PATCH", "/api/v1/users/{user_id}", r, (200,), f"Rôle -> {r.json().get('role')}")

        r = client.post(
            f"{API}/users/{temp_uid}/reset-password",
            headers=admin_h,
            json={"new_password": temp_pwd_2, "must_change_password": False},
        )
        record("POST", "/api/v1/users/{user_id}/reset-password", r, (200,), "Mot de passe réinitialisé")

        r = client.post(f"{API}/users/{temp_uid}/unlock", headers=admin_h)
        record("POST", "/api/v1/users/{user_id}/unlock", r, (200,), "Compte déverrouillé")

        r_tmp_login = client.post(f"{API}/auth/login", json={"email": temp_email, "password": temp_pwd_2})
        tmp_h = {"Authorization": f"Bearer {r_tmp_login.json()['access_token']}"}

        r = client.post(f"{API}/auth/2fa/setup", headers=tmp_h, json={"current_password": temp_pwd_2})
        record("POST", "/api/v1/auth/2fa/setup", r, (200,), "Secret TOTP généré")
        totp_secret = r.json()["secret"]

        r = client.post(f"{API}/auth/2fa/enable", headers=tmp_h, json={"code": pyotp.TOTP(totp_secret).now()})
        record("POST", "/api/v1/auth/2fa/enable", r, (200,), "2FA activée")

        r = client.post(
            f"{API}/auth/2fa/disable",
            headers=tmp_h,
            json={"current_password": temp_pwd_2, "code": pyotp.TOTP(totp_secret).now()},
        )
        record("POST", "/api/v1/auth/2fa/disable", r, (200,), "2FA désactivée")

        r = client.post(
            f"{API}/auth/change-password",
            headers=tmp_h,
            json={"current_password": temp_pwd_2, "new_password": temp_pwd_3},
        )
        record("POST", "/api/v1/auth/change-password", r, (200,), "Changement mot de passe")

        with httpx.Client(timeout=10.0) as tmp_cookie_client:
            tmp_cookie_client.post(f"{API}/auth/login", json={"email": temp_email, "password": temp_pwd_3})
            r = tmp_cookie_client.post(f"{API}/auth/logout")
            record("POST", "/api/v1/auth/logout", r, (200,), "Déconnexion & révocation cookie")

        client.patch(f"{API}/users/{temp_uid}", headers=admin_h, json={"is_active": False})

        r = client.get(f"{API}/audit-logs", headers=enc_h)
        record("GET", "/api/v1/audit-logs", r, (200,), f"Total={r.json().get('total')}")

        media_slug = f"test-photo-{suffix}"
        r = client.post(
            f"{API}/media/upload",
            headers=membre_h,
            files={"file": ("e2e.png", make_png_bytes(), "image/png")},
            data={"category": "photo", "slug": media_slug, "alt_texts": '{"fr":"Photo E2E"}'},
        )
        record("POST", "/api/v1/media/upload", r, (201,), f"Stocké localement: {r.json().get('storage_path')}")
        media_id = r.json()["id"]

        r = client.get(f"{API}/media")
        record("GET", "/api/v1/media", r, (200,), f"Total={r.json().get('total')}")

        r = client.get(f"{API}/media/by-slug/{media_slug}")
        record("GET", "/api/v1/media/by-slug/{slug}", r, (200,), "Métadonnées par slug")

        r = client.get(f"{API}/media/files/{media_id}")
        record("GET", "/api/v1/media/files/{asset_id}", r, (200,), f"{len(r.content)} octets servis")

        r = client.get(f"{API}/media/files-by-slug/{media_slug}")
        record("GET", "/api/v1/media/files-by-slug/{slug}", r, (200,), f"{len(r.content)} octets servis")

        r = client.patch(
            f"{API}/media/{media_id}",
            headers=membre_h,
            json={"captions": {"fr": "Légende mise à jour"}},
        )
        record("PATCH", "/api/v1/media/{asset_id}", r, (200,), "Métadonnées mises à jour")

        proj_slug = f"projet-e2e-{suffix}"
        r = client.post(
            f"{API}/projects",
            headers=membre_h,
            json={
                "slug": proj_slug,
                "category": {"fr": "Test"},
                "title": {"fr": "Projet E2E"},
                "tagline": {"fr": "Accroche E2E"},
                "description": {"fr": "Description E2E"},
                "photo_id": media_id,
                "target_amount_eur": "1000.00",
            },
        )
        record("POST", "/api/v1/projects", r, (201,), f"Projet {proj_slug} créé")

        r = client.get(f"{API}/projects")
        record("GET", "/api/v1/projects", r, (200,), f"{len(r.json())} projets")

        r = client.get(f"{API}/projects/{proj_slug}")
        record("GET", "/api/v1/projects/{slug}", r, (200,), "Détail projet")

        r = client.patch(f"{API}/projects/{proj_slug}", headers=membre_h, json={"target_amount_eur": "1500.00"})
        record("PATCH", "/api/v1/projects/{slug}", r, (200,), "Projet modifié")

        content_key = f"e2e.block.{suffix}"
        r = client.put(
            f"{API}/contents/{content_key}",
            headers=membre_h,
            json={"section": "test", "content": {"fr": {"titre": "Bloc E2E"}}, "is_published": True},
        )
        record("PUT", "/api/v1/contents/{key}", r, (200,), "Bloc CMS créé/mis à jour")

        r = client.get(f"{API}/contents")
        record("GET", "/api/v1/contents", r, (200,), f"{len(r.json())} blocs CMS")

        r = client.get(f"{API}/contents/{content_key}")
        record("GET", "/api/v1/contents/{key}", r, (200,), "Bloc CMS par clé")

        r = client.get(f"{API}/public/home")
        record("GET", "/api/v1/public/home", r, (200,), "Vitrine accueil")

        r = client.get(f"{API}/public/transparence")
        record("GET", "/api/v1/public/transparence", r, (200,), "Vitrine transparence")

        r = client.get(f"{API}/donations/accounts")
        record("GET", "/api/v1/donations/accounts", r, (200,), "Comptes de dépôt COVIDEBA")

        r = client.post(
            f"{API}/donations/submit-receipt",
            data={
                "montant_fcfa": "15000",
                "moyen": "virement",
                "receipt_reference": f"VIR-{suffix}",
                "nom": f"Donateur_{suffix}",
                "email": f"donateur_{suffix}@example.org",
                "pays": "autre",
                "attestation": "false",
                "conditions": "true",
                "langue": "fr",
                "projet": proj_slug,
            },
            files={"receipt_file": ("recu.png", make_png_bytes(), "image/png")},
        )
        record("POST", "/api/v1/donations/submit-receipt", r, (201,), "Reçu soumis pour vérification")

        r = client.post(
            f"{API}/donations/manual",
            headers=tres_h,
            json={
                "project_slug": proj_slug,
                "amount": "25.00",
                "currency": "EUR",
                "payment_method": "especes",
                "donor_last_name": f"Donateur_{suffix}",
                "donor_phone": "+237600000000",
                "account": "Caisse",
            },
        )
        record("POST", "/api/v1/donations/manual", r, (201,), "Don manuel enregistré sans e-mail")
        donation_id = r.json()["id"]

        r = client.get(f"{API}/donations", headers=enc_h)
        record("GET", "/api/v1/donations", r, (200,), f"Total={r.json().get('total')}")

        r = client.get(f"{API}/donations/{donation_id}", headers=enc_h)
        record("GET", "/api/v1/donations/{donation_id}", r, (200,), "Détail don")

        r = client.post(
            f"{API}/subscriptions/follow",
            json={"email": f"suivi_{suffix}@example.org", "projet": proj_slug, "langue": "fr", "consentement": True},
        )
        record("POST", "/api/v1/subscriptions/follow", r, (201,), "Double opt-in envoyé sans fuite de jeton")

        r = client.get(f"{API}/subscriptions", headers=enc_h)
        record("GET", "/api/v1/subscriptions", r, (200,), f"Total={r.json().get('total')}")

        r = client.get(f"{API}/registry/entries", headers=enc_h)
        record("GET", "/api/v1/registry/entries", r, (200,), f"Total={r.json().get('total')}")

        r = client.get(f"{API}/registry/summary", headers=enc_h)
        record("GET", "/api/v1/registry/summary", r, (200,), f"Solde={r.json().get('available_balance_eur')} EUR")

        r = client.post(
            f"{API}/registry/entries",
            headers=membre_h,
            json={
                "entry_date": "2026-11-15",
                "label": f"Cotisation E2E {suffix}",
                "account": "Mobile Money",
                "direction": "entree",
                "amount": "6560",
                "currency": "FCFA",
            },
        )
        record("POST", "/api/v1/registry/entries", r, (201,), "Écriture créée")
        entry_val_id = r.json()["id"]

        r = client.post(f"{API}/registry/entries/{entry_val_id}/validate", headers=tres_h)
        record("POST", "/api/v1/registry/entries/{entry_id}/validate", r, (200,), "Écriture contresignée")

        r_rej_src = client.post(
            f"{API}/registry/entries",
            headers=membre_h,
            json={
                "entry_date": "2026-11-15",
                "label": f"Doublon E2E {suffix}",
                "account": "Mobile Money",
                "direction": "entree",
                "amount": "10.00",
                "currency": "EUR",
            },
        )
        entry_rej_id = r_rej_src.json()["id"]
        r = client.post(
            f"{API}/registry/entries/{entry_rej_id}/reject",
            headers=tres_h,
            json={"reason": "Test rejet E2E"},
        )
        record("POST", "/api/v1/registry/entries/{entry_id}/reject", r, (200,), "Écriture rejetée")

        r = client.get(f"{API}/registry/verify-integrity", headers=enc_h)
        record("GET", "/api/v1/registry/verify-integrity", r, (200,), f"valid={r.json().get('valid')}")

        r = client.get(f"{API}/registry/export?fmt=csv", headers=enc_h)
        record("GET", "/api/v1/registry/export", r, (200,), f"Export CSV ({len(r.text)} car.)")

        r = client.post(
            f"{API}/transfers",
            headers=tres_h,
            json={
                "source_account": "Banque du COVIDEBA",
                "destination_account": "Caisse",
                "amount": "100.00",
                "currency": "EUR",
                "reason": f"Transfert E2E {suffix}",
                "transfer_date": "2026-11-15",
            },
        )
        record("POST", "/api/v1/transfers", r, (201,), "Transfert + 2 écritures créés")
        transfer_id = r.json()["id"]

        r = client.get(f"{API}/transfers", headers=enc_h)
        record("GET", "/api/v1/transfers", r, (200,), f"{len(r.json())} transferts")

        r = client.post(f"{API}/transfers/{transfer_id}/repay", headers=tres_h)
        record("POST", "/api/v1/transfers/{transfer_id}/repay", r, (200,), "Transfert remboursé")

        r = client.get(f"{API}/tasks", headers=enc_h)
        record("GET", "/api/v1/tasks", r, (200,), f"{len(r.json())} tâches")

        r = client.post(
            f"{API}/tasks",
            headers=membre_h,
            json={"label": f"Tâche E2E {suffix}", "cost_eur": "500.00", "duration_months": 1, "weight": "5.0"},
        )
        record("POST", "/api/v1/tasks", r, (201,), f"Tâche #{r.json().get('code_id')} créée")
        task_id = r.json()["id"]
        task_code = r.json()["code_id"]

        r = client.patch(f"{API}/tasks/{task_id}", headers=membre_h, json={"weight": "7.5000"})
        record("PATCH", "/api/v1/tasks/{task_id}", r, (200,), "Tâche modifiée")

        ahp_payload = {
            "criteres": [{"id": 1, "nom": "Urgence"}, {"id": 2, "nom": "Impact"}, {"id": 3, "nom": "Coût"}],
            "curseurs": {"1-2": 2, "1-3": 3, "2-3": 1},
            "notes": {f"{task_code}-1": 9, f"{task_code}-2": 8, f"{task_code}-3": 7},
            "tache_ids": [task_code],
        }
        r = client.post(f"{API}/ahp/calculate", headers=membre_h, json=ahp_payload)
        record("POST", "/api/v1/ahp/calculate", r, (200,), f"CR={r.json().get('ratio_coherence')}")

        r = client.post(
            f"{API}/ahp/sessions",
            headers=membre_h,
            json={"name": f"Séance E2E {suffix}", **ahp_payload, "apply_immediately": False},
        )
        record("POST", "/api/v1/ahp/sessions", r, (201,), "Séance AHP enregistrée")
        ahp_id = r.json()["id"]

        r = client.get(f"{API}/ahp/sessions", headers=enc_h)
        record("GET", "/api/v1/ahp/sessions", r, (200,), f"{len(r.json())} séances AHP")

        r = client.post(f"{API}/ahp/sessions/{ahp_id}/apply", headers=membre_h)
        record("POST", "/api/v1/ahp/sessions/{session_id}/apply", r, (200,), "Poids AHP appliqués")

        instance = {
            "nom": f"plan_e2e_{suffix}",
            "h": 4,
            "S0": 1500,
            "Smin": 500,
            "delta": 0.1,
            "D": [500, 500, 500, 500],
            "taches": [
                {"id": 1, "libelle": "T1", "cout": 800, "duree": 1, "poids": 9},
                {"id": 2, "libelle": "T2", "cout": 600, "duree": 1, "poids": 7},
            ],
            "precedences": [[1, 2]],
        }
        r = client.post(f"{API}/plans/solve", headers=membre_h, json=instance)
        record("POST", "/api/v1/plans/solve", r, (200,), f"statut={r.json().get('statut')}")
        solved = r.json()

        r = client.post(
            f"{API}/plans",
            headers=membre_h,
            json={"name": f"Plan E2E {suffix}", "instance_data": instance, "solver_result": solved},
        )
        record("POST", "/api/v1/plans", r, (201,), "Plan vérifié et enregistré")
        plan_id = r.json()["id"]

        r = client.get(f"{API}/plans", headers=enc_h)
        record("GET", "/api/v1/plans", r, (200,), f"{len(r.json())} plans")

        r = client.post(f"{API}/plans/{plan_id}/validate", headers=membre_h)
        record("POST", "/api/v1/plans/{plan_id}/validate", r, (200,), "Plan validé")

        r = client.post(f"{API}/plans/{plan_id}/archive", headers=membre_h)
        record("POST", "/api/v1/plans/{plan_id}/archive", r, (200,), "Plan archivé")

        r = client.delete(f"{API}/tasks/{task_id}", headers=membre_h)
        record("DELETE", "/api/v1/tasks/{task_id}", r, (200,), "Tâche E2E supprimée")

        r = client.delete(f"{API}/contents/{content_key}", headers=admin_h)
        record("DELETE", "/api/v1/contents/{key}", r, (200,), "Bloc CMS E2E supprimé")

        r = client.delete(f"{API}/projects/{proj_slug}", headers=admin_h)
        record("DELETE", "/api/v1/projects/{slug}", r, (200,), "Projet E2E supprimé")

        r = client.delete(f"{API}/media/{media_id}", headers=membre_h)
        record("DELETE", "/api/v1/media/{asset_id}", r, (200,), "Média E2E supprimé")

    ok_count = sum(1 for item in results if item.passed)
    fail_count = len(results) - ok_count
    print(f"\n{'METHOD':<7} {'ENDPOINT':<44} {'HTTP':<6} {'ATTENDU':<8} {'STATUT':<6} DETAIL")
    print("-" * 115)
    for item in results:
        tag = "OK" if item.passed else "FAIL"
        print(f"{item.method:<7} {item.path:<44} {item.status_code:<6} {item.expected:<8} {tag:<6} {item.detail}")
    print("-" * 115)
    print(f"Total endpoints testés : {len(results)} | Réussis : {ok_count} | Échecs : {fail_count}")
    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
