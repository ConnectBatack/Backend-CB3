from datetime import UTC, datetime

import anyio
from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, PLANNING_EDITOR_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.enums import PlanStatus
from app.models.planning import InvestmentPlan
from app.models.user import User
from app.schemas.planning import PlanInstanceIn, PlanOut, PlanSaveRequest
from app.services.audit import record_audit_log
from app.services.solver_service import resoudre_instance

router = APIRouter(prefix="/plans", tags=["Planification d'investissement (MILP v5_5)"])


@router.post("/solve", summary="Résoudre une instance d'investissement avec HiGHS")
async def solve_plan(
    payload: PlanInstanceIn,
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
) -> dict:
    inst = payload.model_dump(exclude={"limite_temps_s"})
    result = await anyio.to_thread.run_sync(resoudre_instance, inst, payload.limite_temps_s)
    return result


@router.get("", response_model=list[PlanOut], summary="Lister les plans enregistrés")
async def list_plans(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[PlanOut]:
    res = await db.execute(select(InvestmentPlan).order_by(InvestmentPlan.created_at.desc()))
    return [PlanOut.model_validate(p) for p in res.scalars().all()]


@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED, summary="Enregistrer un plan calculé")
async def save_plan(
    payload: PlanSaveRequest,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> PlanOut:
    inst = payload.instance_data.model_dump(exclude={"limite_temps_s"})
    solver_res = payload.solver_result
    if solver_res is None:
        solver_res = await anyio.to_thread.run_sync(
            resoudre_instance, inst, payload.instance_data.limite_temps_s
        )
    if solver_res.get("statut") not in ("optimal", "limite_temps"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Impossible d'enregistrer un plan non réalisable ({solver_res.get('statut')}).",
        )

    plan = InvestmentPlan(
        name=payload.name.strip(),
        instance_data=inst,
        solver_result=solver_res,
        status=PlanStatus.DRAFT,
        created_by_id=current_user.id,
    )
    db.add(plan)
    await db.flush()
    await record_audit_log(
        db,
        action="plans.saved",
        resource_type="investment_plan",
        resource_id=plan.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"name": plan.name, "statut": solver_res.get("statut")},
    )
    await db.commit()
    await db.refresh(plan)
    return PlanOut.model_validate(plan)


@router.post("/{plan_id}/validate", response_model=PlanOut, summary="Valider officiellement un plan d'investissement")
async def validate_plan(
    plan_id: str,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> PlanOut:
    res = await db.execute(select(InvestmentPlan).where(InvestmentPlan.id == plan_id))
    plan = res.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan introuvable.")
    if plan.status == PlanStatus.ARCHIVED:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Un plan archivé ne peut pas être validé.")

    plan.status = PlanStatus.VALIDATED
    plan.validated_by_id = current_user.id
    plan.validated_at = datetime.now(UTC)

    await record_audit_log(
        db,
        action="plans.validated",
        resource_type="investment_plan",
        resource_id=plan.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"name": plan.name},
    )
    await db.commit()
    await db.refresh(plan)
    return PlanOut.model_validate(plan)


@router.post("/{plan_id}/archive", response_model=PlanOut, summary="Archiver un plan")
async def archive_plan(
    plan_id: str,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> PlanOut:
    res = await db.execute(select(InvestmentPlan).where(InvestmentPlan.id == plan_id))
    plan = res.scalar_one_or_none()
    if plan is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Plan introuvable.")
    plan.status = PlanStatus.ARCHIVED
    await record_audit_log(
        db,
        action="plans.archived",
        resource_type="investment_plan",
        resource_id=plan.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(plan)
    return PlanOut.model_validate(plan)

