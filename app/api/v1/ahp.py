from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, PLANNING_EDITOR_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.planning import AhpSession, ProjectTask
from app.models.user import User
from app.schemas.planning import (
    AhpCalculateRequest,
    AhpCalculateResponse,
    AhpSessionOut,
    AhpSessionSaveRequest,
)
from app.services.ahp_service import calculer_ahp
from app.services.audit import record_audit_log

router = APIRouter(prefix="/ahp", tags=["Méthode AHP (Poids des tâches)"])


@router.post("/calculate", response_model=AhpCalculateResponse, summary="Calculer les poids et le ratio de cohérence AHP")
async def calculate_ahp_endpoint(
    payload: AhpCalculateRequest,
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
) -> AhpCalculateResponse:
    try:
        res = calculer_ahp(
            criteres=[c.model_dump() for c in payload.criteres],
            curseurs=payload.curseurs,
            matrice=payload.matrice,
            notes=payload.notes,
            tache_ids=payload.tache_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AhpCalculateResponse(**res)


@router.get("/sessions", response_model=list[AhpSessionOut], summary="Lister les séances AHP enregistrées")
async def list_ahp_sessions(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[AhpSessionOut]:
    res = await db.execute(select(AhpSession).order_by(AhpSession.created_at.desc()))
    return [AhpSessionOut.model_validate(s) for s in res.scalars().all()]


@router.post("/sessions", response_model=AhpSessionOut, status_code=status.HTTP_201_CREATED, summary="Enregistrer une séance AHP")
async def save_ahp_session(
    payload: AhpSessionSaveRequest,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> AhpSessionOut:
    try:
        calc = calculer_ahp(
            criteres=[c.model_dump() for c in payload.criteres],
            curseurs=payload.curseurs,
            matrice=payload.matrice,
            notes=payload.notes,
            tache_ids=payload.tache_ids,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    if payload.apply_immediately and not calc["est_coherent"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible d'appliquer des poids incohérents (CR > 0.1).",
        )

    session = AhpSession(
        name=payload.name.strip(),
        criteria=[c.model_dump() for c in payload.criteres],
        comparison_matrix=calc["matrice"],
        criteria_weights=calc["poids_criteres"],
        consistency_ratio=Decimal(str(calc["ratio_coherence"])),
        is_consistent=calc["est_coherent"],
        task_scores={str(k): v for k, v in calc["poids_taches"].items()},
        applied_to_tasks=payload.apply_immediately,
        created_by_id=current_user.id,
    )
    db.add(session)
    await db.flush()

    if payload.apply_immediately and calc["poids_taches"]:
        for code_id, score in calc["poids_taches"].items():
            t_res = await db.execute(select(ProjectTask).where(ProjectTask.code_id == code_id))
            task = t_res.scalar_one_or_none()
            if task is not None:
                task.weight = Decimal(str(score))

    await record_audit_log(
        db,
        action="ahp.session_saved",
        resource_type="ahp_session",
        resource_id=session.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"is_consistent": calc["est_coherent"], "applied": payload.apply_immediately},
    )
    await db.commit()
    await db.refresh(session)
    return AhpSessionOut.model_validate(session)


@router.post("/sessions/{session_id}/apply", response_model=AhpSessionOut, summary="Appliquer les poids AHP d'une séance aux tâches")
async def apply_ahp_session(
    session_id: str,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> AhpSessionOut:
    res = await db.execute(select(AhpSession).where(AhpSession.id == session_id))
    session = res.scalar_one_or_none()
    if session is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Séance AHP introuvable.")
    if not session.is_consistent:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Impossible d'appliquer une séance AHP incohérente (CR > 0.1).",
        )

    for code_str, score in (session.task_scores or {}).items():
        t_res = await db.execute(select(ProjectTask).where(ProjectTask.code_id == int(code_str)))
        task = t_res.scalar_one_or_none()
        if task is not None:
            task.weight = Decimal(str(score))

    session.applied_to_tasks = True
    await record_audit_log(
        db,
        action="ahp.weights_applied",
        resource_type="ahp_session",
        resource_id=session.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(session)
    return AhpSessionOut.model_validate(session)

