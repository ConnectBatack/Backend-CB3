from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.user import AuditLog, User


async def record_audit_log(
    db: AsyncSession,
    *,
    action: str,
    resource_type: str,
    resource_id: str | None = None,
    actor: User | None = None,
    actor_email: str | None = None,
    ip_address: str | None = None,
    details: dict[str, Any] | None = None,
) -> AuditLog:
    """Enregistre une entrée dans le journal d'audit."""
    entry = AuditLog(
        actor_id=actor.id if actor else None,
        actor_email=actor.email if actor else actor_email,
        actor_role=actor.role.value if actor else None,
        action=action,
        resource_type=resource_type,
        resource_id=resource_id,
        ip_address=ip_address,
        details=details,
    )
    db.add(entry)
    await db.flush()
    return entry

