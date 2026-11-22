"""Support direct deposit receipt submission for donations

Revision ID: 0003_receipt_donations
Revises: 0002_hardening
Create Date: 2026-11-22 22:20:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_receipt_donations"
down_revision: str | Sequence[str] | None = "0002_hardening"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE payment_method ADD VALUE IF NOT EXISTS 'depot_bancaire'")
    with op.batch_alter_table("donations") as batch_op:
        batch_op.add_column(sa.Column("donor_country", sa.String(length=16), nullable=True))
        batch_op.add_column(sa.Column("receipt_media_id", sa.String(length=36), nullable=True))
        if bind.dialect.name == "postgresql":
            batch_op.create_foreign_key(
                "fk_donations_receipt_media_id",
                "media_assets",
                ["receipt_media_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    with op.batch_alter_table("donations") as batch_op:
        batch_op.drop_column("receipt_media_id")
        batch_op.drop_column("donor_country")
