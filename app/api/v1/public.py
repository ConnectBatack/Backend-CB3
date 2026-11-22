from decimal import Decimal

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.projects import to_project_out
from app.core.config import PARITE_FCFA, get_settings
from app.core.database import get_db
from app.models.content import Project, SiteContent
from app.models.enums import LedgerDirection, LedgerStatus, ProjectStatus
from app.models.finance import LedgerEntry
from app.models.media import MediaAsset
from app.schemas.finance import PublicLedgerItemOut, TransparencyOut
from app.services.ledger_service import (
    compute_project_ledger_stats,
    compute_registry_summary,
    resolve_effective_operations,
)
from app.services.settings_service import get_effective_organization_settings

router = APIRouter(prefix="/public", tags=["Vitrine publique"])


def _public_safe_label(entry: LedgerEntry) -> str:
    """Garantit qu'aucune donnée personnelle (nom de donateur) n'apparaît dans la vitrine publique."""
    if entry.donation_id is not None:
        return "Don vérifié"
    return entry.label


@router.get("/home", summary="Données complètes de la page d'accueil")
async def get_public_home(db: AsyncSession = Depends(get_db)) -> dict:
    org = await get_effective_organization_settings(db)
    proj_res = await db.execute(
        select(Project)
        .where(Project.status != ProjectStatus.ARCHIVED)
        .order_by(Project.display_order.asc(), Project.created_at.asc())
    )
    stats = await compute_project_ledger_stats(db)
    projects = [
        to_project_out(
            p,
            collected_eur=stats.get(p.id, (Decimal("0.00"), 0))[0],
            contributors_count=stats.get(p.id, (Decimal("0.00"), 0))[1],
        )
        for p in proj_res.scalars().all()
    ]
    featured_need = next((p for p in projects if p.is_featured_need), projects[0] if projects else None)

    contents_res = await db.execute(
        select(SiteContent).where(SiteContent.is_published.is_(True), SiteContent.section == "accueil")
    )
    contents = {c.key: c.content for c in contents_res.scalars().all()}

    return {
        "featured_need": featured_need,
        "projects": projects,
        "contents": contents,
        "contact": {
            "email": org.covideba_email,
            "phone": org.covideba_phone,
            "postal_address": org.postal_address,
            "other_info": org.other_info,
        },
        "payment_accounts": {
            "bank_holder": org.bank_holder,
            "bank_name": org.bank_name,
            "bank_account": org.bank_account,
            "mobile_money_mtn": org.mobile_money_mtn,
            "mobile_money_orange": org.mobile_money_orange,
        },
    }


@router.get("/transparence", response_model=TransparencyOut, summary="Données publiques de transparence financière")
async def get_public_transparency(db: AsyncSession = Depends(get_db)) -> TransparencyOut:
    settings = get_settings()
    prefix = settings.API_V1_PREFIX.rstrip("/")
    summary = await compute_registry_summary(db)

    projects_count = (
        await db.execute(select(func.count(Project.id)).where(Project.status != ProjectStatus.ARCHIVED))
    ).scalar_one()

    all_res = await db.execute(select(LedgerEntry).order_by(LedgerEntry.entry_number.asc()))
    resolved = resolve_effective_operations(list(all_res.scalars().all()))

    validated_ops = [
        item["entry"]
        for item in resolved
        if item["effective_status"] == LedgerStatus.VALIDEE
    ]
    donors_count = sum(
        1
        for e in validated_ops
        if e.direction == LedgerDirection.ENTREE and e.donation_id is not None
    )

    recent_ops = sorted(validated_ops, key=lambda e: e.entry_number, reverse=True)[:30]
    media_ids = [e.justificatif_media_id for e in recent_ops if e.justificatif_media_id]
    public_media_ids: set[str] = set()
    if media_ids:
        m_res = await db.execute(
            select(MediaAsset.id).where(MediaAsset.id.in_(media_ids), MediaAsset.is_public.is_(True))
        )
        public_media_ids = set(m_res.scalars().all())

    recent_entries = [
        PublicLedgerItemOut(
            entry_number=e.entry_number,
            entry_date=e.entry_date,
            label=_public_safe_label(e),
            account=e.account,
            direction=e.direction,
            amount_eur_equiv=Decimal(e.amount_eur_equiv),
            entry_hash=e.entry_hash,
            justificatif_url=(
                f"{prefix}/media/files/{e.justificatif_media_id}"
                if e.justificatif_media_id and e.justificatif_media_id in public_media_ids
                else None
            ),
        )
        for e in recent_ops
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
