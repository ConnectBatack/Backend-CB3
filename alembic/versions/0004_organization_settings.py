"""Add organization_settings table and user phone column

Revision ID: 0004_organization_settings
Revises: 0003_receipt_donations
Create Date: 2026-11-22 22:35:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_organization_settings"
down_revision: str | Sequence[str] | None = "0003_receipt_donations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("users") as batch_op:
        batch_op.add_column(sa.Column("phone", sa.String(length=32), nullable=True))

    op.create_table(
        "organization_settings",
        sa.Column("key", sa.String(length=32), nullable=False),
        sa.Column("covideba_email", sa.String(length=254), nullable=False),
        sa.Column("covideba_phone", sa.String(length=64), nullable=False),
        sa.Column("bank_holder", sa.String(length=160), nullable=False),
        sa.Column("bank_name", sa.String(length=160), nullable=False),
        sa.Column("bank_account", sa.String(length=120), nullable=False),
        sa.Column("mobile_money_mtn", sa.String(length=64), nullable=False),
        sa.Column("mobile_money_orange", sa.String(length=64), nullable=False),
        sa.Column("postal_address", sa.String(length=255), nullable=True),
        sa.Column("other_info", sa.String(length=1000), nullable=True),
        sa.Column("updated_by_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["updated_by_id"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("organization_settings")
    with op.batch_alter_table("users") as batch_op:
        batch_op.drop_column("phone")

