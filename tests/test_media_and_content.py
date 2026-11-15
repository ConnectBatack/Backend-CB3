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
async def test_local_media_upload_serve_update_delete(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")
    encadrant_h = await login_as(client, "encadrant@batack.org", "EncadrantPass2026!")

    png_bytes = _make_test_png(40, 30)

    # Encadrant en lecture seule ne peut pas téléverser
    forbidden = await client.post(
        "/api/v1/media/upload",
        headers=encadrant_h,
        files={"file": ("test.png", png_bytes, "image/png")},
        data={"category": "photo"},
    )
    assert forbidden.status_code == 403

    # Upload valide par un membre
    up = await client.post(
        "/api/v1/media/upload",
        headers=membre_h,
        files={"file": ("dispensaire-test.png", png_bytes, "image/png")},
        data={
            "category": "photo",
            "slug": "dispensaire-test",
            "alt_texts": json.dumps({"fr": "Photo test", "en": "Test photo"}),
            "is_public": "true",
        },
    )
    assert up.status_code == 201, up.text
    asset = up.json()
    assert asset["width"] == 40
    assert asset["height"] == 30
    assert asset["slug"] == "dispensaire-test"

    # Fichier servi publiquement par ID et par slug
    f1 = await client.get(f"/api/v1/media/files/{asset['id']}")
    assert f1.status_code == 200
    assert f1.content == png_bytes

    f2 = await client.get("/api/v1/media/files-by-slug/dispensaire-test")
    assert f2.status_code == 200
    assert f2.content == png_bytes

    # Refuser un fichier corrompu prétendant être une image
    bad = await client.post(
        "/api/v1/media/upload",
        headers=membre_h,
        files={"file": ("fake.jpg", b"not-an-image", "image/jpeg")},
        data={"category": "photo"},
    )
    assert bad.status_code == 400

    # Suppression
    deleted = await client.delete(f"/api/v1/media/{asset['id']}", headers=membre_h)
    assert deleted.status_code == 200
    f_after = await client.get(f"/api/v1/media/files/{asset['id']}")
    assert f_after.status_code == 404


@pytest.mark.asyncio
async def test_projects_cms_and_public_endpoints(client: AsyncClient, staff_users):
    membre_h = await login_as(client, "membre@batack.org", "MembrePass2026!")

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
            "collected_amount_eur": "4200.00",
            "contributors_count": 37,
            "is_featured_need": True,
        },
    )
    assert proj.status_code == 201
    assert proj.json()["progress_percent"] == 35

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

    transp = await client.get("/api/v1/public/transparence")
    assert transp.status_code == 200
    assert transp.json()["projects_count"] == 1

