import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.enums import PlanStatus, TaskStatus

JSON_TYPE = JSON().with_variant(JSONB, "postgresql")


def utcnow() -> datetime:
    return datetime.now(UTC)


class ProjectTask(Base):
    """Tâches candidates à la planification (solveur MILP v5_5 et notation AHP)."""

    __tablename__ = "project_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    code_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    project_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("projects.id", ondelete="SET NULL"), nullable=True, index=True
    )
    label: Mapped[str] = mapped_column(String(200), nullable=False)
    cost_eur: Mapped[Decimal] = mapped_column(Numeric(15, 2), nullable=False)
    duration_months: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    weight: Mapped[Decimal] = mapped_column(Numeric(12, 4), nullable=False, default=Decimal("1.0000"))
    status: Mapped[TaskStatus] = mapped_column(
        Enum(TaskStatus, values_callable=lambda e: [x.value for x in e], name="task_status"),
        nullable=False,
        default=TaskStatus.CANDIDATE,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=utcnow, onupdate=utcnow
    )


class TaskPrecedence(Base):
    """Relation de précédence entre deux tâches (predecessor_code -> successor_code)."""

    __tablename__ = "task_precedences"
    __table_args__ = (UniqueConstraint("predecessor_id", "successor_id", name="uq_task_precedence"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    predecessor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False
    )
    successor_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False
    )


class AhpSession(Base):
    """Séance d'analyse multicritère AHP (Saaty) enregistrée pour calculer les poids des tâches."""

    __tablename__ = "ahp_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    criteria: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)  # [{"id": 1, "nom": "..."}]
    comparison_matrix: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)  # n x n
    criteria_weights: Mapped[list] = mapped_column(JSON_TYPE, nullable=False)
    consistency_ratio: Mapped[Decimal] = mapped_column(Numeric(8, 5), nullable=False)
    is_consistent: Mapped[bool] = mapped_column(Boolean, nullable=False)
    task_scores: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False, default=dict)  # {task_code_id: score}
    applied_to_tasks: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)


class InvestmentPlan(Base):
    """Plan d'investissement calculé par le solveur MILP v5_5 et validé par le COVIDEBA."""

    __tablename__ = "investment_plans"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    instance_data: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    solver_result: Mapped[dict] = mapped_column(JSON_TYPE, nullable=False)
    status: Mapped[PlanStatus] = mapped_column(
        Enum(PlanStatus, values_callable=lambda e: [x.value for x in e], name="plan_status"),
        nullable=False,
        default=PlanStatus.DRAFT,
        index=True,
    )
    created_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    validated_by_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    validated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, default=utcnow)

