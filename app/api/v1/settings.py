from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, require_admin, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.user import User
from app.schemas.finance import OrganizationSettingsOut, OrganizationSettingsUpdate
from app.services.settings_service import (
    get_effective_organization_settings,
    update_organization_settings,
)

router = APIRouter(prefix="/settings", tags=["Administration - Paramètres du site & comptes"])


@router.get(
    "/organization",
    response_model=OrganizationSettingsOut,
    summary="Consulter les coordonnées et numéros de compte du COVIDEBA",
)
async def read_organization_settings(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> OrganizationSettingsOut:
    return await get_effective_organization_settings(db)


@router.put(
    "/organization",
    response_model=OrganizationSettingsOut,
    summary="Modifier les coordonnées et numéros de compte du COVIDEBA (admin)",
)
async def write_organization_settings(
    payload: OrganizationSettingsUpdate,
    request: Request,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
) -> OrganizationSettingsOut:
    return await update_organization_settings(
        db,
        payload=payload,
        actor=admin,
        ip_address=client_ip(request),
    )

