from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import ALL_STAFF_ROLES, PLANNING_EDITOR_ROLES, require_roles
from app.core.database import get_db
from app.core.rate_limit import client_ip
from app.models.planning import ProjectTask, TaskPrecedence
from app.models.user import User
from app.schemas.common import MessageResponse
from app.schemas.planning import TaskCreate, TaskOut, TaskUpdate
from app.services.audit import record_audit_log

router = APIRouter(prefix="/tasks", tags=["Tâches candidates"])


async def _task_to_out(db: AsyncSession, task: ProjectTask) -> TaskOut:
    res = await db.execute(
        select(ProjectTask.code_id)
        .join(TaskPrecedence, TaskPrecedence.predecessor_id == ProjectTask.id)
        .where(TaskPrecedence.successor_id == task.id)
    )
    preds = sorted(res.scalars().all())
    return TaskOut(
        id=task.id,
        code_id=task.code_id,
        project_id=task.project_id,
        label=task.label,
        cost_eur=task.cost_eur,
        duration_months=task.duration_months,
        weight=task.weight,
        status=task.status,
        predecessor_code_ids=preds,
        created_at=task.created_at,
        updated_at=task.updated_at,
    )


@router.get("", response_model=list[TaskOut], summary="Lister les tâches de planification")
async def list_tasks(
    _: User = Depends(require_roles(*ALL_STAFF_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> list[TaskOut]:
    res = await db.execute(select(ProjectTask).order_by(ProjectTask.code_id.asc()))
    tasks = list(res.scalars().all())
    return [await _task_to_out(db, t) for t in tasks]


@router.post("", response_model=TaskOut, status_code=status.HTTP_201_CREATED, summary="Créer une tâche")
async def create_task(
    payload: TaskCreate,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    code_id = payload.code_id
    if code_id is None:
        max_code = (await db.execute(select(func.max(ProjectTask.code_id)))).scalar_one() or 0
        code_id = max_code + 1
    else:
        dup = await db.execute(select(ProjectTask).where(ProjectTask.code_id == code_id))
        if dup.scalar_one_or_none() is not None:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Ce code_id de tâche existe déjà.")

    task = ProjectTask(
        code_id=code_id,
        project_id=payload.project_id,
        label=payload.label.strip(),
        cost_eur=payload.cost_eur,
        duration_months=payload.duration_months,
        weight=payload.weight,
        status=payload.status,
        created_by_id=current_user.id,
    )
    db.add(task)
    await db.flush()

    for pred_code in set(payload.predecessor_code_ids):
        if pred_code == code_id:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Une tâche ne peut pas se précéder elle-même.")
        pred_res = await db.execute(select(ProjectTask).where(ProjectTask.code_id == pred_code))
        pred_task = pred_res.scalar_one_or_none()
        if pred_task is None:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Prédécesseur #{pred_code} introuvable.")
        db.add(TaskPrecedence(predecessor_id=pred_task.id, successor_id=task.id))

    await record_audit_log(
        db,
        action="tasks.created",
        resource_type="project_task",
        resource_id=task.id,
        actor=current_user,
        ip_address=client_ip(request),
        details={"code_id": code_id, "label": task.label},
    )
    await db.commit()
    await db.refresh(task)
    return await _task_to_out(db, task)


@router.patch("/{task_id}", response_model=TaskOut, summary="Modifier une tâche")
async def update_task(
    task_id: str,
    payload: TaskUpdate,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> TaskOut:
    res = await db.execute(select(ProjectTask).where(ProjectTask.id == task_id))
    task = res.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tâche introuvable.")

    updates = payload.model_dump(exclude_unset=True, exclude={"predecessor_code_ids"})
    for k, v in updates.items():
        setattr(task, k, v)

    if payload.predecessor_code_ids is not None:
        old_precs = await db.execute(select(TaskPrecedence).where(TaskPrecedence.successor_id == task.id))
        for p in old_precs.scalars().all():
            await db.delete(p)
        await db.flush()
        for pred_code in set(payload.predecessor_code_ids):
            if pred_code == task.code_id:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Auto-précédence interdite.")
            pred_res = await db.execute(select(ProjectTask).where(ProjectTask.code_id == pred_code))
            pred_task = pred_res.scalar_one_or_none()
            if pred_task is None:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Prédécesseur #{pred_code} introuvable.")
            db.add(TaskPrecedence(predecessor_id=pred_task.id, successor_id=task.id))

    await record_audit_log(
        db,
        action="tasks.updated",
        resource_type="project_task",
        resource_id=task.id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    await db.refresh(task)
    return await _task_to_out(db, task)


@router.delete("/{task_id}", response_model=MessageResponse, summary="Supprimer une tâche")
async def delete_task(
    task_id: str,
    request: Request,
    current_user: User = Depends(require_roles(*PLANNING_EDITOR_ROLES)),
    db: AsyncSession = Depends(get_db),
) -> MessageResponse:
    res = await db.execute(select(ProjectTask).where(ProjectTask.id == task_id))
    task = res.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tâche introuvable.")
    await db.delete(task)
    await record_audit_log(
        db,
        action="tasks.deleted",
        resource_type="project_task",
        resource_id=task_id,
        actor=current_user,
        ip_address=client_ip(request),
    )
    await db.commit()
    return MessageResponse(message="Tâche supprimée.")

