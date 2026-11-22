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
    DonationFrequency,
    DonationStatus,
    LedgerDirection,
    LedgerStatus,
    MediaCategory,
    PaymentMethod,
    PlanStatus,
    ProjectStatus,
    UserRole,
)
from app.models.finance import Donation, LedgerEntry
from app.models.media import MediaAsset
from app.models.planning import InvestmentPlan, ProjectTask, TaskPrecedence
from app.models.user import User
from app.services.default_projects import EMBEDDED_PROJECTS_I18N
from app.services.ledger_service import create_ledger_entry

logger = logging.getLogger("batack.seed")


def _resolve_images_dir() -> Path | None:
    settings = get_settings()
    root = Path(__file__).resolve().parents[3]
    candidates = []
    if settings.FRONTEND_IMAGES_DIR:
        candidates.append(Path(settings.FRONTEND_IMAGES_DIR))
    candidates.append(root / "connectbatack3-site" / "public" / "images")
    candidates.append(root / "frontend" / "public" / "images")
    for c in candidates:
        if c.exists() and c.is_dir():
            return c
    return None


def _load_frontend_i18n() -> dict[str, dict]:
    """Charge les dictionnaires multilingues si le chemin configurable est présent."""
    settings = get_settings()
    root = Path(__file__).resolve().parents[3]
    langs = ("fr", "en", "es", "nl")
    result: dict[str, dict] = {}
    candidates = []
    if settings.FRONTEND_I18N_DIR:
        candidates.append(Path(settings.FRONTEND_I18N_DIR))
    candidates.append(root / "connectbatack3-site" / "src" / "i18n")
    candidates.append(root / "frontend" / "src" / "i18n")

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

    # 3. Projets vitrine (tous les 6 projets du village avec textes multilingues)
    default_projects = [
        ("dispensaire", "dispensaire-facade", Decimal("12195.92"), True, 1),
        ("batteries", "panneau-solaire", Decimal("3048.98"), False, 2),
        ("ecole", "salle-de-classe", Decimal("1219.59"), False, 3),
        ("eau", "batack-paysage", Decimal("5030.82"), False, 4),
        ("marche", "marche", Decimal("3963.67"), False, 5),
        ("route", "batack-paysage", Decimal("8003.57"), False, 6),
    ]
    projects_by_slug: dict[str, Project] = {}
    for slug, photo_slug, target, featured, order in default_projects:
        embedded = EMBEDDED_PROJECTS_I18N.get(slug, {})
        cat_dict = dict(embedded.get("category", {}))
        title_dict = dict(embedded.get("title", {}))
        tag_dict = dict(embedded.get("tagline", {}))
        desc_dict = dict(embedded.get("description", {}))

        for lang, d in i18n.items():
            p_txt = (d.get("projets") or {}).get(slug)
            if p_txt:
                cat_dict[lang] = p_txt.get("categorie", cat_dict.get(lang, ""))
                title_dict[lang] = p_txt.get("titre", title_dict.get(lang, slug))
                tag_dict[lang] = p_txt.get("accroche", tag_dict.get(lang, ""))
                desc_dict[lang] = p_txt.get("description", desc_dict.get(lang, ""))
        if not title_dict:
            title_dict = {"fr": slug.capitalize()}

        photo_asset = media_by_slug.get(photo_slug)

        res = await db.execute(select(Project).where(Project.slug == slug))
        existing_p = res.scalar_one_or_none()
        if existing_p is not None:
            if not existing_p.category:
                existing_p.category = cat_dict
            if not existing_p.title or existing_p.title == {"fr": slug.capitalize()}:
                existing_p.title = title_dict
            if not existing_p.tagline:
                existing_p.tagline = tag_dict
            if not existing_p.description:
                existing_p.description = desc_dict
            if existing_p.photo_id is None and photo_asset is not None:
                existing_p.photo_id = photo_asset.id
            projects_by_slug[slug] = existing_p
            continue

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

    # 5. Tâches, plan initial, dons & écritures de démonstration en développement
    if include_demo_data and settings.APP_ENV == "development":
        existing_tasks = await db.execute(select(ProjectTask).limit(1))
        if existing_tasks.scalar_one_or_none() is None:
            disp = projects_by_slug.get("dispensaire")
            sample_tasks = [
                (1, "Toiture du dispensaire", Decimal("2500.00"), 2, Decimal("9.0")),
                (2, "Câblage électrique", Decimal("1200.00"), 1, Decimal("8.0")),
                (3, "Matériel d'analyse", Decimal("2000.00"), 1, Decimal("7.0")),
                (4, "Matériel de radiologie", Decimal("1500.00"), 2, Decimal("6.0")),
                (5, "Entretien des batteries solaires", Decimal("400.00"), 1, Decimal("5.0")),
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

        existing_plan = await db.execute(select(InvestmentPlan).limit(1))
        if existing_plan.scalar_one_or_none() is None:
            db.add(
                InvestmentPlan(
                    name="plan_initial_dispensaire",
                    instance_data={
                        "nom": "plan_initial_dispensaire",
                        "h": 6,
                        "S0": 2100000,
                        "Smin": 330000,
                        "delta": 0.1,
                        "D": [590000, 590000, 590000, 590000, 590000, 590000],
                    },
                    solver_result={},
                    status=PlanStatus.DRAFT,
                    created_by_id=admin_user.id if admin_user else None,
                )
            )

        existing_ledger = await db.execute(select(LedgerEntry).limit(1))
        if existing_ledger.scalar_one_or_none() is None:
            disp = projects_by_slug.get("dispensaire")
            batt = projects_by_slug.get("batteries")
            ecole = projects_by_slug.get("ecole")
            eau = projects_by_slug.get("eau")

            # Dons initiaux validés rattachés aux projets pour alimenter vitrine et transparence
            demo_donations = [
                ("dispensaire", disp, Decimal("4192.35"), PaymentMethod.VIREMENT, "VIR-INIT-2026-01", date(2026, 10, 15), "Banque du COVIDEBA", "Don initial pour le dispensaire"),
                ("batteries", batt, Decimal("914.69"), PaymentMethod.MOBILE_MONEY, "MM-INIT-2026-02", date(2026, 10, 22), "Mobile Money", "Contribution énergie solaire"),
                ("ecole", ecole, Decimal("647.91"), PaymentMethod.DEPOT_BANCAIRE, "DEP-INIT-2026-03", date(2026, 10, 26), "Banque du COVIDEBA", "Soutien école primaire"),
                ("eau", eau, Decimal("304.90"), PaymentMethod.VIREMENT, "VIR-INIT-2026-04", date(2026, 10, 29), "Banque du COVIDEBA", "Soutien réseau eau potable"),
            ]
            for idx, (slug, proj_obj, amt_eur, pmethod, ref, d_date, acc, lbl) in enumerate(demo_donations, start=1):
                don = Donation(
                    project_id=proj_obj.id if proj_obj else None,
                    amount_eur=amt_eur,
                    original_amount=amt_eur,
                    currency=Currency.EUR,
                    frequency=DonationFrequency.PONCTUEL,
                    payment_method=pmethod,
                    status=DonationStatus.PAID,
                    donor_first_name="Donateur",
                    donor_last_name=f"COVIDEBA {idx}",
                    donor_email=f"donateur{idx}@example.org",
                    donor_country="BE",
                    language="fr",
                    receipt_reference=ref,
                )
                db.add(don)
                await db.flush()
                await create_ledger_entry(
                    db,
                    entry_date=d_date,
                    label=f"{lbl} ({slug})",
                    account=acc,
                    direction=LedgerDirection.ENTREE,
                    amount=amt_eur,
                    currency=Currency.EUR,
                    initial_status=LedgerStatus.VALIDEE,
                    project_id=proj_obj.id if proj_obj else None,
                    donation_id=don.id,
                    receipt_number=ref,
                    actor=admin_user,
                )
                stats["ledger"] += 1

            extra_entries = [
                (date(2026, 11, 2), "Achat de câbles électriques", "Caisse", LedgerDirection.SORTIE, Decimal("45000.00"), Currency.FCFA, LedgerStatus.VALIDEE, disp.id if disp else None),
                (date(2026, 11, 6), "Cotisation Mobile Money", "Mobile Money", LedgerDirection.ENTREE, Decimal("32800.00"), Currency.FCFA, LedgerStatus.A_CONTRESIGNER, disp.id if disp else None),
            ]
            for d_date, lbl, acc, dir_, amt, cur, st, pid in extra_entries:
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
