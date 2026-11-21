import hashlib
import json
import logging
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

from PIL import Image
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import FORBIDDEN_PASSWORDS, get_settings
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

logger = logging.getLogger("batack.seed")


def _resolve_images_dir() -> Path | None:
    settings = get_settings()
    candidates = []
    if settings.FRONTEND_IMAGES_DIR:
        candidates.append(Path(settings.FRONTEND_IMAGES_DIR))
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "public" / "images")
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return None


def _load_frontend_i18n() -> dict[str, dict]:
    """Charge les dictionnaires multilingues si le chemin configurable est présent."""
    settings = get_settings()
    langs = ("fr", "en", "es", "nl")
    result: dict[str, dict] = {}
    candidates = []
    if settings.FRONTEND_I18N_DIR:
        candidates.append(Path(settings.FRONTEND_I18N_DIR))
    candidates.append(Path(__file__).resolve().parents[3] / "frontend" / "src" / "i18n")

    base = next((c for c in candidates if c.exists() and c.is_dir()), None)
    if base is None:
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
    """
    Initialise de manière idempotente l'administrateur (avec changement de mot de passe
    obligatoire), les médias locaux si disponibles, les projets et données de base.
    Aucun mot de passe n'est codé en dur dans ce script.
    """
    settings = get_settings()
    stats = {"users": 0, "media": 0, "projects": 0, "contents": 0, "tasks": 0, "ledger": 0}

    # 1. Administrateur initial (uniquement si ADMIN_DEFAULT_PASSWORD est explicitement configuré)
    admin_user: User | None = None
    admin_pwd = settings.ADMIN_DEFAULT_PASSWORD.strip()
    if admin_pwd:
        if admin_pwd in FORBIDDEN_PASSWORDS or admin_pwd.startswith("CHANGE_ME"):
            raise RuntimeError("ADMIN_DEFAULT_PASSWORD utilise un mot de passe interdit/générique.")
        norm_email = settings.ADMIN_DEFAULT_EMAIL.strip().lower()
        res = await db.execute(select(User).where(User.email == norm_email))
        admin_user = res.scalar_one_or_none()
        if admin_user is None:
            admin_user = User(
                email=norm_email,
                full_name=settings.ADMIN_DEFAULT_NAME,
                role=UserRole.ADMIN,
                password_hash=hash_password(admin_pwd),
                is_active=True,
                must_change_password=True,
            )
            db.add(admin_user)
            await db.flush()
            stats["users"] += 1
    else:
        res = await db.execute(select(User).where(User.role == UserRole.ADMIN).limit(1))
        admin_user = res.scalar_one_or_none()

    # Comptes de démo optionnels uniquement si SEED_DEMO_PASSWORD est fourni en dev
    membre_user: User | None = None
    demo_pwd = os.getenv("SEED_DEMO_PASSWORD", "").strip()
    if include_demo_data and settings.APP_ENV == "development" and demo_pwd:
        if demo_pwd in FORBIDDEN_PASSWORDS:
            raise RuntimeError("SEED_DEMO_PASSWORD utilise un mot de passe interdit.")
        for email, full_name, role in [
            ("tresorier@batack.org", "Trésorier COVIDEBA", UserRole.TRESORIER),
            ("membre@batack.org", "Membre COVIDEBA", UserRole.MEMBRE),
            ("encadrant@batack.org", "Encadrant UMONS", UserRole.ENCADRANT),
        ]:
            res = await db.execute(select(User).where(User.email == email))
            u = res.scalar_one_or_none()
            if u is None:
                u = User(
                    email=email,
                    full_name=full_name,
                    role=role,
                    password_hash=hash_password(demo_pwd),
                    is_active=True,
                    must_change_password=True,
                )
                db.add(u)
                await db.flush()
                stats["users"] += 1
            if role == UserRole.MEMBRE:
                membre_user = u

    # 2. Importation optionnelle des photos & logos depuis FRONTEND_IMAGES_DIR
    i18n = _load_frontend_i18n()
    storage = LocalStorageManager()
    storage.ensure_directories()

    images_dir = _resolve_images_dir()
    media_by_slug: dict[str, MediaAsset] = {}
    if images_dir is not None:
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
    else:
        logger.info("Aucun dossier d'images frontend détecté (FRONTEND_IMAGES_DIR non défini ou absent).")

    # 3. Projets vitrine
    default_projects = [
        ("dispensaire", "dispensaire-facade", Decimal("12000.00"), True, 1),
        ("batteries", "panneau-solaire", Decimal("3000.00"), False, 2),
        ("ecole", "salle-de-classe", Decimal("1200.00"), False, 3),
        ("eau", "batack-paysage", Decimal("5000.00"), False, 4),
        ("marche", "marche", Decimal("4000.00"), False, 5),
        ("route", "batack-paysage", Decimal("8000.00"), False, 6),
    ]
    projects_by_slug: dict[str, Project] = {}
    for slug, photo_slug, target, featured, order in default_projects:
        res = await db.execute(select(Project).where(Project.slug == slug))
        existing_p = res.scalar_one_or_none()
        if existing_p is not None:
            projects_by_slug[slug] = existing_p
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
            collected_amount_eur=Decimal("0.00"),
            contributors_count=0,
            is_featured_need=featured,
            display_order=order,
            status=ProjectStatus.ACTIVE,
            created_by_id=admin_user.id if admin_user else None,
        )
        db.add(proj)
        await db.flush()
        projects_by_slug[slug] = proj
        stats["projects"] += 1

    # 4. Contenus éditoriaux
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

    # 5. Tâches & écritures d'exemple uniquement en développement
    if include_demo_data and settings.APP_ENV == "development":
        existing_tasks = await db.execute(select(ProjectTask).limit(1))
        if existing_tasks.scalar_one_or_none() is None:
            disp = projects_by_slug.get("dispensaire")
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

        existing_ledger = await db.execute(select(LedgerEntry).limit(1))
        if existing_ledger.scalar_one_or_none() is None:
            disp = projects_by_slug.get("dispensaire")
            sample_entries = [
                (date(2026, 10, 15), "Solde initial reporté", "Banque du COVIDEBA", LedgerDirection.ENTREE, Decimal("2000.00"), Currency.EUR, LedgerStatus.VALIDEE, disp.id if disp else None),
                (date(2026, 10, 28), "Reversement de l'institution agréée", "Banque du COVIDEBA", LedgerDirection.ENTREE, Decimal("1200.00"), Currency.EUR, LedgerStatus.VALIDEE, disp.id if disp else None),
                (date(2026, 11, 2), "Achat de câbles électriques", "Caisse", LedgerDirection.SORTIE, Decimal("45000.00"), Currency.FCFA, LedgerStatus.VALIDEE, disp.id if disp else None),
                (date(2026, 11, 6), "Cotisation Mobile Money", "Mobile Money", LedgerDirection.ENTREE, Decimal("32800.00"), Currency.FCFA, LedgerStatus.A_CONTRESIGNER, disp.id if disp else None),
                (date(2026, 11, 8), "Don en ligne (Stripe)", "Fonds chez l'institution agréée", LedgerDirection.ENTREE, Decimal("50.00"), Currency.EUR, LedgerStatus.VALIDEE, disp.id if disp else None),
            ]
            for d_date, lbl, acc, dir_, amt, cur, st, pid in sample_entries:
                await create_ledger_entry(
                    db,
                    entry_date=d_date,
                    label=lbl,
                    account=acc,
                    direction=dir_,
                    amount=amt,
                    currency=cur,
                    initial_status=st,
                    project_id=pid,
                    actor=membre_user if st == LedgerStatus.A_CONTRESIGNER else admin_user,
                )
                stats["ledger"] += 1

    await db.commit()
    return stats
