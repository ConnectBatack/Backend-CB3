from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import PARITE_FCFA
from app.models.content import OrganizationSettings
from app.models.user import User
from app.schemas.finance import OrganizationSettingsOut, OrganizationSettingsUpdate
from app.services.audit import record_audit_log

SETTINGS_KEY = "default"


def _empty_settings() -> dict[str, str | None]:
    return {
        "covideba_email": "",
        "covideba_phone": "",
        "bank_holder": "",
        "bank_name": "",
        "bank_account": "",
        "mobile_money_mtn": "",
        "mobile_money_orange": "",
        "postal_address": None,
        "other_info": None,
    }


async def get_effective_organization_settings(db: AsyncSession) -> OrganizationSettingsOut:
    """Retourne les coordonnées et comptes configurés en base (chaînes vides tant qu'ils ne sont pas saisis)."""
    res = await db.execute(select(OrganizationSettings).where(OrganizationSettings.key == SETTINGS_KEY))
    row = res.scalar_one_or_none()

    if row is None:
        return OrganizationSettingsOut(
            **_empty_settings(),
            parity_fcfa_per_eur=PARITE_FCFA,
            updated_at=None,
        )

    return OrganizationSettingsOut(
        covideba_email=row.covideba_email,
        covideba_phone=row.covideba_phone,
        bank_holder=row.bank_holder,
        bank_name=row.bank_name,
        bank_account=row.bank_account,
        mobile_money_mtn=row.mobile_money_mtn,
        mobile_money_orange=row.mobile_money_orange,
        postal_address=row.postal_address,
        other_info=row.other_info,
        parity_fcfa_per_eur=PARITE_FCFA,
        updated_at=row.updated_at,
    )


async def update_organization_settings(
    db: AsyncSession,
    *,
    payload: OrganizationSettingsUpdate,
    actor: User,
    ip_address: str | None,
) -> OrganizationSettingsOut:
    """Crée ou met à jour la ligne unique de paramètres du COVIDEBA avec trace d'audit."""
    res = await db.execute(select(OrganizationSettings).where(OrganizationSettings.key == SETTINGS_KEY))
    row = res.scalar_one_or_none()

    if row is None:
        row = OrganizationSettings(
            key=SETTINGS_KEY,
            covideba_email="",
            covideba_phone="",
            bank_holder="",
            bank_name="",
            bank_account="",
            mobile_money_mtn="",
            mobile_money_orange="",
        )
        db.add(row)

    updates = payload.model_dump(exclude_unset=True)
    changed_fields: list[str] = []
    for field, value in updates.items():
        if value is not None:
            cleaned = value.strip()
            if field == "covideba_email":
                cleaned = cleaned.lower()
            setattr(row, field, cleaned or None if field in {"postal_address", "other_info"} else cleaned)
            changed_fields.append(field)

    row.updated_by_id = actor.id
    await db.flush()
    await record_audit_log(
        db,
        action="settings.organization_updated",
        resource_type="organization_settings",
        resource_id=SETTINGS_KEY,
        actor=actor,
        ip_address=ip_address,
        details={"changed_fields": changed_fields},
    )
    await db.commit()
    await db.refresh(row)
    return await get_effective_organization_settings(db)
