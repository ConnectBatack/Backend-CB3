import pyotp
import pytest
from httpx import AsyncClient

from app.core.config import Settings
from tests.conftest import login_as


@pytest.mark.asyncio
async def test_login_cookie_refresh_logout_and_jwt_revocation(client: AsyncClient, staff_users):
    resp = await client.post(
        "/api/v1/auth/login",
        json={"email": "admin@batack.org", "password": "AdminPass2026!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    access_header = {"Authorization": f"Bearer {data['access_token']}"}
    assert "cb3_session" in resp.cookies

    me_cookie = await client.get("/api/v1/auth/me")
    assert me_cookie.status_code == 200

    ref = await client.post("/api/v1/auth/refresh", json={"refresh_token": data["refresh_token"]})
    assert ref.status_code == 200

    # Déconnexion -> révoque le cookie ET le JWT d'accès lié à la session
    out = await client.post("/api/v1/auth/logout", headers=access_header)
    assert out.status_code == 200
    assert (await client.get("/api/v1/auth/me")).status_code == 401
    assert (await client.get("/api/v1/auth/me", headers=access_header)).status_code == 401


@pytest.mark.asyncio
async def test_rate_limit_no_account_lockout_and_ignore_spoofed_xff(client: AsyncClient, staff_users):
    r_unknown = await client.post(
        "/api/v1/auth/login",
        json={"email": "nobody@batack.org", "password": "WrongPassword123!"},
    )
    r_wrong = await client.post(
        "/api/v1/auth/login",
        json={"email": "membre@batack.org", "password": "WrongPassword123!"},
    )
    assert r_unknown.status_code == 401
    assert r_wrong.status_code == 401
    assert r_unknown.json()["detail"] == r_wrong.json()["detail"]

    # Même en forgeant X-Forwarded-For, le rate limiter utilise l'IP réelle (TRUST_PROXY_HEADERS=false)
    for i in range(4):
        await client.post(
            "/api/v1/auth/login",
            headers={"X-Forwarded-For": f"10.0.0.{i + 1}"},
            json={"email": "membre@batack.org", "password": "WrongPassword123!"},
        )

    r_limited = await client.post(
        "/api/v1/auth/login",
        headers={"X-Forwarded-For": "10.0.0.99"},
        json={"email": "membre@batack.org", "password": "MembrePass2026!"},
    )
    assert r_limited.status_code == 429


@pytest.mark.asyncio
async def test_must_change_password_enforcement(client: AsyncClient, staff_users):
    admin_h = await login_as(client, "admin@batack.org", "AdminPass2026!")
    created = await client.post(
        "/api/v1/users",
        headers=admin_h,
        json={
            "email": "forcechange@batack.org",
            "full_name": "Force Change",
            "role": "membre",
            "password": "TempPassword2026!",
            "must_change_password": True,
        },
    )
    assert created.status_code == 201

    user_h = await login_as(client, "forcechange@batack.org", "TempPassword2026!")
    # Bloqué sur les autres routes tant que le mot de passe n'est pas changé
    assert (await client.get("/api/v1/tasks", headers=user_h)).status_code == 403
    assert (await client.get("/api/v1/projects", headers=user_h)).status_code == 403

    # Autorisé sur /auth/change-password
    changed = await client.post(
        "/api/v1/auth/change-password",
        headers=user_h,
        json={"current_password": "TempPassword2026!", "new_password": "NewStrongPassword2026!"},
    )
    assert changed.status_code == 200
    # L'ancien JWT est immédiatement invalidé après changement de mot de passe
    assert (await client.get("/api/v1/auth/me", headers=user_h)).status_code == 401
    assert (await client.get("/api/v1/tasks", headers=user_h)).status_code == 401

    new_h = await login_as(client, "forcechange@batack.org", "NewStrongPassword2026!")
    assert (await client.get("/api/v1/projects", headers=new_h)).status_code == 200


@pytest.mark.asyncio
async def test_admin_user_and_role_management(client: AsyncClient, staff_users):
    admin_h = await login_as(client, "admin@batack.org", "AdminPass2026!")
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")

    assert (await client.get("/api/v1/users", headers=membre_h)).status_code == 403

    created = await client.post(
        "/api/v1/users",
        headers=admin_h,
        json={
            "email": "nouveau@batack.org",
            "full_name": "Nouveau Membre",
            "role": "membre",
            "password": "StrongPassword2026!",
        },
    )
    assert created.status_code == 201
    uid = created.json()["id"]

    patched = await client.patch(f"/api/v1/users/{uid}", headers=admin_h, json={"role": "tresorier"})
    assert patched.status_code == 200
    assert patched.json()["role"] == "tresorier"

    encadrant_h = await login_as(client, "encadrant@batack.org", "EncadrantPass2026!")
    audits = await client.get("/api/v1/audit-logs", headers=encadrant_h)
    assert audits.status_code == 200
    assert audits.json()["total"] >= 1


@pytest.mark.asyncio
async def test_totp_2fa_cannot_be_overwritten_and_requires_password(client: AsyncClient, staff_users):
    tres_h = await login_as(client, "tresorier@batack.org", "TresorierPass2026!")

    # Mot de passe requis sur /2fa/setup
    bad_setup = await client.post(
        "/api/v1/auth/2fa/setup", headers=tres_h, json={"current_password": "WrongPassword123!"}
    )
    assert bad_setup.status_code == 401

    setup = await client.post(
        "/api/v1/auth/2fa/setup", headers=tres_h, json={"current_password": "TresorierPass2026!"}
    )
    assert setup.status_code == 200
    secret = setup.json()["secret"]

    enable = await client.post(
        "/api/v1/auth/2fa/enable", headers=tres_h, json={"code": pyotp.TOTP(secret).now()}
    )
    assert enable.status_code == 200

    # Impossible de réinitialiser la 2FA via /2fa/setup quand elle est déjà active
    overwrite_attempt = await client.post(
        "/api/v1/auth/2fa/setup", headers=tres_h, json={"current_password": "TresorierPass2026!"}
    )
    assert overwrite_attempt.status_code == 409

    # Connexion sans code TOTP refusée
    fail_login = await client.post(
        "/api/v1/auth/login",
        json={"email": "tresorier@batack.org", "password": "TresorierPass2026!"},
    )
    assert fail_login.status_code == 401

    # Connexion avec code TOTP acceptée
    ok_login = await client.post(
        "/api/v1/auth/login",
        json={
            "email": "tresorier@batack.org",
            "password": "TresorierPass2026!",
            "totp_code": pyotp.TOTP(secret).now(),
        },
    )
    assert ok_login.status_code == 200

    # Désactivation exige mot de passe + code TOTP
    bad_disable = await client.post(
        "/api/v1/auth/2fa/disable",
        headers=tres_h,
        json={"current_password": "WrongPassword123!", "code": pyotp.TOTP(secret).now()},
    )
    assert bad_disable.status_code == 401

    ok_disable = await client.post(
        "/api/v1/auth/2fa/disable",
        headers=tres_h,
        json={"current_password": "TresorierPass2026!", "code": pyotp.TOTP(secret).now()},
    )
    assert ok_disable.status_code == 200


def test_production_startup_security_guards():
    s = Settings(
        APP_ENV="production",
        SECRET_KEY="batack_dev_secret_key_change_in_production_min_48_chars_2026",
        SESSION_COOKIE_SECURE=True,
    )
    with pytest.raises(RuntimeError):
        s.validate_startup_security()

    s2 = Settings(
        APP_ENV="production",
        SECRET_KEY="a" * 48,
        SESSION_COOKIE_SECURE=False,
    )
    with pytest.raises(RuntimeError):
        s2.validate_startup_security()
