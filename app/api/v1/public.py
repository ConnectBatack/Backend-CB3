from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import to_project_out
from app.core.config import PARITE_FCFA, get_settings
from app.core.database import get_db
from app.models.content import Project, SiteContent
from app.models.enums import DonationStatus, LedgerStatus, ProjectStatus
from app.models.finance import Donation, LedgerEntry
from app.schemas.finance import PublicLedgerItemOut, TransparencyOut
from app.services.ledger_service import compute_registry_summary

router = APIRouter(prefix="/public", tags=["Vitrine publique"])


@router.get("/home", summary="Données complètes de la page d'accueil")
async def get_public_home(db: AsyncSession = Depends(get_db)) -> dict:
    proj_res = await db.execute(
        select(Project)
        .where(Project.status != ProjectStatus.ARCHIVED)
        .order_by(Project.display_order.asc(), Project.created_at.asc())
    )
    projects = [to_project_out(p) for p in proj_res.scalars().all()]
    featured_need = next((p for p in projects if p.is_featured_need), projects[0] if projects else None)

    contents_res = await db.execute(
        select(SiteContent).where(SiteContent.is_published.is_(True), SiteContent.section == "accueil")
    )
    contents = {c.key: c.content for c in contents_res.scalars().all()}

    return {
        "featured_need": featured_need,
        "projects": projects,
        "contents": contents,
    }


@router.get("/transparence", response_model=TransparencyOut, summary="Données publiques de transparence financière")
async def get_public_transparency(db: AsyncSession = Depends(get_db)) -> TransparencyOut:
    settings = get_settings()
    prefix = settings.API_V1_PREFIX.rstrip("/")
    summary = await compute_registry_summary(db)

    donors_count = (
        await db.execute(select(func.count(Donation.id)).where(Donation.status == DonationStatus.PAID))
    ).scalar_one()
    projects_count = (
        await db.execute(select(func.count(Project.id)).where(Project.status != ProjectStatus.ARCHIVED))
    ).scalar_one()

    entries_res = await db.execute(
        select(LedgerEntry)
        .where(LedgerEntry.status == LedgerStatus.VALIDEE)
        .order_by(LedgerEntry.entry_number.desc())
        .limit(30)
    )
    recent_entries = [
        PublicLedgerItemOut(
            entry_number=e.entry_number,
            entry_date=e.entry_date,
            label=e.label,
            account=e.account,
            direction=e.direction,
            amount_eur_equiv=Decimal(e.amount_eur_equiv),
            entry_hash=e.entry_hash,
            justificatif_url=f"{prefix}/media/files/{e.justificatif_media_id}" if e.justificatif_media_id else None,
        )
        for e in entries_res.scalars().all()
    ]

    return TransparencyOut(
        total_collected_eur=summary["total_validated_in_eur"],
        total_spent_eur=summary["total_validated_out_eur"],
        available_balance_eur=summary["available_balance_eur"],
        donors_count=donors_count,
        projects_count=projects_count,
        parity_fcfa_per_eur=PARITE_FCFA,
        recent_entries=recent_entries,
    )

