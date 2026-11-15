import hashlib
import json
from datetime import date
from decimal import Decimal
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.security import hash_password
from app.core.storage import LocalStorageManager
from app.models.content import Project, SiteContent
from app.models.enums import (
    Currency,
    LedgerDirection,
    LedgerStatus,
    MediaCategory,
    ProjectStatus,
    UserRole,
)
from app.models.finance import LedgerEntry
from app.models.media import MediaAsset
from app.models.planning import ProjectTask, TaskPrecedence
from app.models.user import User
from app.services.ledger_service import create_ledger_entry


def _locate_frontend_dir() -> Path | None:
    """Repère les ressources du frontend (en local ou montées dans Docker)."""
    candidates = [
        Path("/app/frontend_images").parent,  # si monté dans Docker
        Path(__file__).resolve().parents[3] / "frontend",
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


def _load_frontend_i18n() -> dict[str, dict]:
    """Charge les dictionnaires fr/en/es/nl du frontend si disponibles pour pré-remplir les textes."""
    langs = ("fr", "en", "es", "nl")
    result: dict[str, dict] = {}
    docker_i18n = Path("/app/frontend_i18n")
    local_frontend = Path(__file__).resolve().parents[3] / "frontend" / "src" / "i18n"
    base = docker_i18n if docker_i18n.exists() else local_frontend
    if not base.exists():
        return result
    for lang in langs:
        f = base / f"{lang}.json"
        if f.exists():
            try:
                result[lang] = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                pass
    return result


async def seed_database(db: AsyncSession, include_demo_data: bool = True) -> dict[str, int]:
    """Initialise de manière idempotente l'administrateur, les médias locaux, les projets et données de base."""
    settings = get_settings()
    stats = {"users": 0, "media": 0, "projects": 0, "contents": 0, "tasks": 0, "ledger": 0}

    # 1. Administrateur par défaut (+ comptes démo en développement)
    users_to_seed = [
        (settings.ADMIN_DEFAULT_EMAIL, settings.ADMIN_DEFAULT_NAME, UserRole.ADMIN, settings.ADMIN_DEFAULT_PASSWORD),
    ]
    if include_demo_data and settings.APP_ENV == "development":
        users_to_seed.extend(
            [
                ("tresorier@batack.org", "Trésorier COVIDEBA", UserRole.TRESORIER, "BatackTresorier2026!"),
                ("membre@batack.org", "Membre COVIDEBA", UserRole.MEMBRE, "BatackMembre2026!"),
                ("encadrant@batack.org", "Encadrant UMONS", UserRole.ENCADRANT, "BatackEncadrant2026!"),
            ]
        )

    admin_user: User | None = None
    membre_user: User | None = None
    for email, full_name, role, pwd in users_to_seed:
        norm_email = email.strip().lower()
        res = await db.execute(select(User).where(User.email == norm_email))
        existing = res.scalar_one_or_none()
        if existing is None:
            u = User(
                email=norm_email,
                full_name=full_name,
                role=role,
                password_hash=hash_password(pwd),
                is_active=True,
            )
            db.add(u)
            await db.flush()
            stats["users"] += 1
            existing = u
        if role == UserRole.ADMIN:
            admin_user = existing
        if role == UserRole.MEMBRE:
            membre_user = existing

    # 2. Importation des photos & logos depuis frontend/public/images vers backend/storage/
    i18n = _load_frontend_i18n()
    storage = LocalStorageManager()
    storage.ensure_directories()

    docker_imgs = Path("/app/frontend_images")
    local_imgs = Path(__file__).resolve().parents[3] / "frontend" / "public" / "images"
    images_dir = docker_imgs if docker_imgs.exists() else local_imgs

    media_by_slug: dict[str, MediaAsset] = {}
    if images_dir.exists():
        image_files = list(images_dir.glob("*.jpg")) + list((images_dir / "logos").glob("*.png"))
        for img_path in sorted(image_files):
            slug = img_path.stem
            res = await db.execute(select(MediaAsset).where(MediaAsset.slug == slug))
            existing_media = res.scalar_one_or_none()
            if existing_media is not None:
                media_by_slug[slug] = existing_media
                continue

            is_logo = img_path.parent.name == "logos"
            category = MediaCategory.LOGO if is_logo else MediaCategory.PHOTO
            mime = "image/png" if img_path.suffix.lower() == ".png" else "image/jpeg"
            data = img_path.read_bytes()
            width, height = None, None
            try:
                with Image.open(img_path) as im:
                    width, height = im.size
            except Exception:
                pass

            info = storage.save_bytes(
                data=data,
                original_filename=img_path.name,
                mime_type=mime,
                ext=img_path.suffix.lower(),
                category=category,
                width=width,
                height=height,
            )

            alt_texts: dict[str, str] = {}
            for lang, dict_data in i18n.items():
                alt = (dict_data.get("photos") or {}).get(slug)
                if alt:
                    alt_texts[lang] = alt
            if not alt_texts:
                alt_texts = {"fr": slug.replace("-", " ").capitalize()}

            asset = MediaAsset(
                slug=slug,
                category=category,
                filename=info.filename,
                original_filename=info.original_filename,
                storage_path=info.relative_path,
                mime_type=info.mime_type,
                size_bytes=info.size_bytes,
                sha256=hashlib.sha256(data).hexdigest(),
                width=info.width,
                height=info.height,
                alt_texts=alt_texts,
                captions={},
                is_public=True,
                uploaded_by_id=admin_user.id if admin_user else None,
            )
            db.add(asset)
            await db.flush()
            media_by_slug[slug] = asset
            stats["media"] += 1

    # 3. Projets vitrine (alignés avec frontend/src/donnees/projets.js)
    default_projects = [
        ("dispensaire", "dispensaire-facade", Decimal("4200.00"), Decimal("12000.00"), 37, True, 1),
        ("batteries", "panneau-solaire", Decimal("900.00"), Decimal("3000.00"), 12, False, 2),
        ("ecole", "salle-de-classe", Decimal("650.00"), Decimal("1200.00"), 9, False, 3),
        ("eau", "batack-paysage", Decimal("300.00"), Decimal("5000.00"), 4, False, 4),
        ("marche", "marche", Decimal("0.00"), Decimal("4000.00"), 0, False, 5),
        ("route", "batack-paysage", Decimal("0.00"), Decimal("8000.00"), 0, False, 6),
    ]
    for slug, photo_slug, collected, target, contributors, featured, order in default_projects:
        res = await db.execute(select(Project).where(Project.slug == slug))
        if res.scalar_one_or_none() is not None:
            continue

        cat_dict, title_dict, tag_dict, desc_dict = {}, {}, {}, {}
        for lang, d in i18n.items():
            p_txt = (d.get("projets") or {}).get(slug)
            if p_txt:
                cat_dict[lang] = p_txt.get("categorie", "")
                title_dict[lang] = p_txt.get("titre", slug)
                tag_dict[lang] = p_txt.get("accroche", "")
                desc_dict[lang] = p_txt.get("description", "")
        if not title_dict:
            title_dict = {"fr": slug.capitalize()}

        photo_asset = media_by_slug.get(photo_slug)
        proj = Project(
            slug=slug,
            category=cat_dict,
            title=title_dict,
            tagline=tag_dict,
            description=desc_dict,
            photo_id=photo_asset.id if photo_asset else None,
            photo_slug=photo_slug,
            target_amount_eur=target,
            collected_amount_eur=collected,
            contributors_count=contributors,
            is_featured_need=featured,
            display_order=order,
            status=ProjectStatus.ACTIVE,
            created_by_id=admin_user.id if admin_user else None,
        )
        db.add(proj)
        await db.flush()
        stats["projects"] += 1

    # 4. Contenus éditoriaux de base (diaporama accueil, prochain besoin)
    if i18n:
        contents_seed = [
            (
                "accueil.diaporama",
                "accueil",
                {
                    "photos": [
                        "batack-paysage",
                        "accueil-chants",
                        "installation-electrique",
                        "ecole-publique",
                        "marche",
                    ]
                },
            ),
            (
                "accueil.prochain_besoin",
                "accueil",
                {
                    "projet_slug": "dispensaire",
                    "collecte_eur": 1400,
                    "objectif_eur": 4000,
                    "textes": {
                        lang: {
                            "titre": (d.get("accueil") or {}).get("besoinTitre", ""),
                            "texte": (d.get("accueil") or {}).get("besoinTexte", ""),
                        }
                        for lang, d in i18n.items()
                    },
                },
            ),
        ]
        for key, section, payload in contents_seed:
            res = await db.execute(select(SiteContent).where(SiteContent.key == key))
            if res.scalar_one_or_none() is None:
                db.add(
                    SiteContent(
                        key=key,
                        section=section,
                        content=payload,
                        is_published=True,
                        updated_by_id=admin_user.id if admin_user else None,
                    )
                )
                stats["contents"] += 1

    # 5. Tâches d'exemple et précédences (alignées avec frontend/src/donnees/exemple-gestion.js)
    if include_demo_data:
        existing_tasks = await db.execute(select(ProjectTask).limit(1))
        if existing_tasks.scalar_one_or_none() is None:
            disp_res = await db.execute(select(Project).where(Project.slug == "dispensaire"))
            disp = disp_res.scalar_one_or_none()
            sample_tasks = [
                (1, "Toiture du dispensaire", Decimal("2500.00"), 2, Decimal("9.0")),
                (2, "Câblage électrique", Decimal("1200.00"), 1, Decimal("8.0")),
                (3, "Matériel d'analyse", Decimal("2000.00"), 1, Decimal("7.0")),
                (4, "Entretien des batteries", Decimal("400.00"), 1, Decimal("6.0")),
                (5, "Salaire d'un instituteur (trimestre)", Decimal("900.00"), 3, Decimal("7.0")),
                (6, "Citerne d'eau", Decimal("1500.00"), 2, Decimal("5.0")),
            ]
            created_tasks: dict[int, ProjectTask] = {}
            for code_id, label, cost, dur, weight in sample_tasks:
                t = ProjectTask(
                    code_id=code_id,
                    project_id=disp.id if disp else None,
                    label=label,
                    cost_eur=cost,
                    duration_months=dur,
                    weight=weight,
                    created_by_id=membre_user.id if membre_user else (admin_user.id if admin_user else None),
                )
                db.add(t)
                await db.flush()
                created_tasks[code_id] = t
                stats["tasks"] += 1
            for pred_code, succ_code in [(1, 2), (2, 3)]:
                db.add(
                    TaskPrecedence(
                        predecessor_id=created_tasks[pred_code].id,
                        successor_id=created_tasks[succ_code].id,
                    )
                )

        # 6. Écritures initiales du registre si vide
        existing_ledger = await db.execute(select(LedgerEntry).limit(1))
        if existing_ledger.scalar_one_or_none() is None:
            sample_entries = [
                (date(2026, 10, 15), "Solde initial reporté", "Banque du COVIDEBA", LedgerDirection.ENTREE, Decimal("2000.00"), Currency.EUR, LedgerStatus.VALIDEE),
                (date(2026, 10, 28), "Reversement de l'institution agréée", "Banque du COVIDEBA", LedgerDirection.ENTREE, Decimal("1200.00"), Currency.EUR, LedgerStatus.VALIDEE),
                (date(2026, 11, 2), "Achat de câbles électriques", "Caisse", LedgerDirection.SORTIE, Decimal("45000.00"), Currency.FCFA, LedgerStatus.VALIDEE),
                (date(2026, 11, 6), "Cotisation Mobile Money", "Mobile Money", LedgerDirection.ENTREE, Decimal("32800.00"), Currency.FCFA, LedgerStatus.A_CONTRESIGNER),
                (date(2026, 11, 8), "Don en ligne (Stripe)", "Fonds chez l'institution agréée", LedgerDirection.ENTREE, Decimal("50.00"), Currency.EUR, LedgerStatus.VALIDEE),
            ]
            for d_date, lbl, acc, dir_, amt, cur, st in sample_entries:
                await create_ledger_entry(
                    db,
                    entry_date=d_date,
                    label=lbl,
                    account=acc,
                    direction=dir_,
                    amount=amt,
                    currency=cur,
                    initial_status=st,
                    actor=membre_user if st == LedgerStatus.A_CONTRESIGNER else admin_user,
                )
                stats["ledger"] += 1

    await db.commit()
    return stats

