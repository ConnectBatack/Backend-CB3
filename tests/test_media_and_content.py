import io
import json

import pytest
from httpx import AsyncClient
from PIL import Image

from tests.conftest import login_as


def _make_test_png(width: int = 32, height: int = 24) -> bytes:
    buf = io.BytesIO()
    img = Image.new("RGB", (width, height), color=(34, 139, 34))
    img.save(buf, format="PNG")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_local_media_upload_private_justificatifs_and_delete(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    encadrant_h = await login_as(client, "encadrant@batack.org", "EncadrantPass2026!")

    png_bytes = _make_test_png(40, 30)

    forbidden = await client.post(
        "/api/v1/media/upload",
        headers=encadrant_h,
        files={"file": ("test.png", png_bytes, "image/png")},
        data={"category": "photo"},
    )
    assert forbidden.status_code == 403

    # Photo publique par défaut
    up = await client.post(
        "/api/v1/media/upload",
        headers=membre_h,
        files={"file": ("dispensaire-test.png", png_bytes, "image/png")},
        data={
            "category": "photo",
            "slug": "dispensaire-test",
            "alt_texts": json.dumps({"fr": "Photo test", "en": "Test photo"}),
        },
    )
    assert up.status_code == 201
    asset = up.json()
    assert asset["is_public"] is True
    assert (await client.get(f"/api/v1/media/files/{asset['id']}")).status_code == 200
    assert (await client.get("/api/v1/media/files-by-slug/dispensaire-test")).content == png_bytes

    # Justificatif PDF privé par défaut (refusé sans authentification)
    pdf_bytes = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF"
    up_just = await client.post(
        "/api/v1/media/upload",
        headers=membre_h,
        files={"file": ("recu.pdf", pdf_bytes, "application/pdf")},
        data={"category": "justificatif"},
    )
    assert up_just.status_code == 201
    just = up_just.json()
    assert just["is_public"] is False
    assert (await client.get(f"/api/v1/media/files/{just['id']}")).status_code == 401
    assert (await client.get(f"/api/v1/media/files/{just['id']}", headers=encadrant_h)).status_code == 200

    # Suppression
    assert (await client.delete(f"/api/v1/media/{asset['id']}", headers=membre_h)).status_code == 200
    assert (await client.get(f"/api/v1/media/files/{asset['id']}")).status_code == 404


@pytest.mark.asyncio
async def test_projects_cms_plain_text_and_public_endpoints(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")

    # Rejet HTML dans les contenus CMS et projets
    bad_proj = await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={
            "slug": "xss",
            "title": {"fr": "<script>alert(1)</script>"},
            "target_amount_eur": "100.00",
        },
    )
    assert bad_proj.status_code == 422

    bad_cms = await client.put(
        "/api/v1/contents/accueil.xss",
        headers=membre_h,
        json={"section": "accueil", "content": {"fr": {"titre": "<b>HTML</b>"}}},
    )
    assert bad_cms.status_code == 422

    proj = await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={
            "slug": "dispensaire",
            "category": {"fr": "Santé", "en": "Health"},
            "title": {"fr": "Achever le dispensaire", "en": "Finish clinic"},
            "tagline": {"fr": "Soins de proximité"},
            "description": {"fr": "Description complète."},
            "photo_slug": "dispensaire-facade",
            "target_amount_eur": "12000.00",
            "is_featured_need": True,
        },
    )
    assert proj.status_code == 201
    assert proj.json()["collected_amount_eur"] == "0.00"
    assert proj.json()["contributors_count"] == 0

    cms = await client.put(
        "/api/v1/contents/accueil.hero",
        headers=membre_h,
        json={
            "section": "accueil",
            "content": {"fr": {"titre": "Aidez Batack"}},
            "is_published": True,
        },
    )
    assert cms.status_code == 200

    home = await client.get("/api/v1/public/home")
    assert home.status_code == 200
    assert home.json()["featured_need"]["slug"] == "dispensaire"
    assert "accueil.hero" in home.json()["contents"]


@pytest.mark.asyncio
async def test_seed_creates_all_six_multilingual_projects(db_session, client: AsyncClient):
    from app.services.seed import seed_database

    stats = await seed_database(db_session, include_demo_data=False)
    assert stats["projects"] == 6

    res = await client.get("/api/v1/projects")
    assert res.status_code == 200
    items = res.json()
    slugs = [p["slug"] for p in items]
    assert slugs == ["dispensaire", "batteries", "ecole", "eau", "marche", "route"]
    for p in items:
        assert p["title"].get("fr") and p["title"].get("en")
        assert p["description"].get("fr") and p["description"].get("en")


@pytest.mark.asyncio
async def test_project_auto_slug_and_google_translate_completion(
    client: AsyncClient, staff_users, monkeypatch
):
    from app.services import translate_service

    async def fake_translate(text: str, source_lang: str, target_lang: str, *, client=None):
        return f"[{target_lang}] {text}", True

    monkeypatch.setattr(translate_service, "translate_text", fake_translate)
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")

    tr = await client.post(
        "/api/v1/projects/translate",
        headers=membre_h,
        json={
            "source_language": "fr",
            "category": "Eau",
            "title": "Forage du village",
            "tagline": "Eau potable pour tous",
            "description": "Construction d'un puits communautaire.",
        },
    )
    assert tr.status_code == 200
    tr_data = tr.json()
    assert tr_data["translated_with_google"] is True
    assert tr_data["title"]["en"] == "[en] Forage du village"
    assert tr_data["title"]["es"] == "[es] Forage du village"
    assert tr_data["title"]["nl"] == "[nl] Forage du village"

    created = await client.post(
        "/api/v1/projects",
        headers=membre_h,
        json={
            "source_language": "fr",
            "auto_translate": True,
            "category": {"fr": "Eau"},
            "title": {"fr": "Forage d'eau potable"},
            "tagline": {"fr": "Eau propre au village"},
            "description": {"fr": "Accès durable à l'eau."},
            "target_amount_eur": "4500.00",
            "photo_slug": "batack-paysage",
        },
    )
    assert created.status_code == 201, created.text
    proj = created.json()
    assert proj["slug"] == "forage-d-eau-potable"
    assert proj["title"]["en"] == "[en] Forage d'eau potable"
    assert proj["description"]["nl"] == "[nl] Accès durable à l'eau."
