from datetime import datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import PlanStatus, TaskStatus


class TaskCreate(BaseModel):
    code_id: int | None = Field(default=None, ge=1)
    project_id: str | None = None
    label: str = Field(min_length=1, max_length=200)
    cost_eur: Decimal = Field(ge=0, le=Decimal("1000000000"))
    duration_months: int = Field(default=1, ge=1, le=120)
    weight: Decimal = Field(default=Decimal("1.0000"), ge=0, le=Decimal("1000000"))
    status: TaskStatus = TaskStatus.CANDIDATE
    predecessor_code_ids: list[int] = Field(default_factory=list)


class TaskUpdate(BaseModel):
    project_id: str | None = None
    label: str | None = Field(default=None, min_length=1, max_length=200)
    cost_eur: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000000"))
    duration_months: int | None = Field(default=None, ge=1, le=120)
    weight: Decimal | None = Field(default=None, ge=0, le=Decimal("1000000"))
    status: TaskStatus | None = None
    predecessor_code_ids: list[int] | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    code_id: int
    project_id: str | None
    label: str
    cost_eur: Decimal
    duration_months: int
    weight: Decimal
    status: TaskStatus
    predecessor_code_ids: list[int] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AhpCriterionIn(BaseModel):
    id: int = Field(ge=1)
    nom: str = Field(min_length=1, max_length=60)


class AhpCalculateRequest(BaseModel):
    criteres: list[AhpCriterionIn] = Field(min_length=2, max_length=10)
    # comparaisons["1-2"] = curseur (-8..8) ou matrice complète
    curseurs: dict[str, int] | None = None
    matrice: list[list[float]] | None = None
    # notes["task_code_id-crit_id"] = 0..10
    notes: dict[str, float] = Field(default_factory=dict)
    tache_ids: list[int] = Field(default_factory=list)


class AhpCalculateResponse(BaseModel):
    matrice: list[list[float]]
    poids_criteres: list[float]
    lambda_max: float
    ratio_coherence: float
    est_coherent: bool
    seuil_coherence: float
    poids_taches: dict[int, float]


class AhpSessionSaveRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    criteres: list[AhpCriterionIn] = Field(min_length=2, max_length=10)
    curseurs: dict[str, int] | None = None
    matrice: list[list[float]] | None = None
    notes: dict[str, float] = Field(default_factory=dict)
    tache_ids: list[int] = Field(default_factory=list)
    apply_immediately: bool = False


class AhpSessionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    criteria: list[dict[str, Any]]
    comparison_matrix: list[list[float]]
    criteria_weights: list[float]
    consistency_ratio: Decimal
    is_consistent: bool
    task_scores: dict[str, float]
    applied_to_tasks: bool
    created_by_id: str | None
    created_at: datetime


class SolverTaskIn(BaseModel):
    id: int
    libelle: str = Field(default="", max_length=200)
    cout: float = Field(ge=0, le=1e9)
    duree: int = Field(ge=1)
    poids: float = Field(ge=0, le=1e6)


class PlanInstanceIn(BaseModel):
    nom: str = Field(default="plan_covideba", max_length=160)
    h: int = Field(ge=1, le=36)
    S0: float = Field(ge=0, le=1e9)
    Smin: float = Field(ge=0, le=1e9)
    delta: float = Field(ge=0, le=1)
    D: list[float]
    taches: list[SolverTaskIn]
    precedences: list[list[int]] = Field(default_factory=list)
    limite_temps_s: float = Field(default=30.0, gt=0, le=120.0)


class PlanSaveRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    instance_data: PlanInstanceIn
    solver_result: dict[str, Any] | None = None


class PlanOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    instance_data: dict[str, Any]
    solver_result: dict[str, Any]
    status: PlanStatus
    created_by_id: str | None
    validated_by_id: str | None
    validated_at: datetime | None
    created_at: datetime

