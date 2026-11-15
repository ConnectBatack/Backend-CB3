from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AUDIT_READ_ROLES, require_roles
from app.core.database import get_db
from app.models.user import AuditLog, User
from app.schemas.auth import AuditLogOut
from app.schemas.common import PaginatedResponse

router = APIRouter(prefix="/audit-logs", tags=["Administration - Journal d'audit"])


@router.get("", response_model=PaginatedResponse[AuditLogOut], summary="Consulter le journal d'audit")
async def list_audit_logs(
    action: str | None = None,
    resource_type: str | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require_roles(*AUDIT_READ_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> PaginatedResponse[AuditLogOut]:
    query = select(AuditLog)
    count_query = select(func.count(AuditLog.id))
    if action:
        query = query.where(AuditLog.action == action)
        count_query = count_query.where(AuditLog.action == action)
    if resource_type:
        query = query.where(AuditLog.resource_type == resource_type)
        count_query = count_query.where(AuditLog.resource_type == resource_type)

    total = (await db.execute(count_query)).scalar_one()
    res = await db.execute(query.order_by(AuditLog.created_at.desc()).limit(limit).offset(offset))
    items = [AuditLogOut.model_validate(item) for item in res.scalars().all()]
    return PaginatedResponse(items=items, total=total, limit=limit, offset=offset)

