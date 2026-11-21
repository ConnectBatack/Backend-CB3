"""Security and ledger immutability hardening

Revision ID: 0002_hardening
Revises: 0001_initial
Create Date: 2026-11-20 22:15:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_hardening"
down_revision: str | Sequence[str] | None = "0001_initial"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        op.execute("ALTER TYPE donation_status ADD VALUE IF NOT EXISTS 'disputed'")
        sa.Enum("operation", "validation", "rejet", name="ledger_entry_type").create(
            bind, checkfirst=True
        )

    with op.batch_alter_table("donations") as batch_op:
        batch_op.alter_column("donor_email", existing_type=sa.String(length=254), nullable=True)
        batch_op.add_column(sa.Column("donor_phone", sa.String(length=32), nullable=True))
        batch_op.add_column(
            sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index("ix_donations_stripe_subscription_id", ["stripe_subscription_id"])

    with op.batch_alter_table("ledger_entries") as batch_op:
        batch_op.add_column(
            sa.Column(
                "entry_type",
                sa.Enum("operation", "validation", "rejet", name="ledger_entry_type"),
                nullable=False,
                server_default="operation",
            )
        )
        batch_op.add_column(sa.Column("transfer_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("target_entry_id", sa.String(length=36), nullable=True))
        batch_op.create_index("ix_ledger_entries_entry_type", ["entry_type"])
        batch_op.create_index("ix_ledger_entries_transfer_id", ["transfer_id"])
        batch_op.create_index("ix_ledger_entries_target_entry_id", ["target_entry_id"])
        if bind.dialect.name == "postgresql":
            batch_op.create_foreign_key(
                "fk_ledger_entries_transfer_id",
                "fund_transfers",
                ["transfer_id"],
                ["id"],
                ondelete="SET NULL",
            )
            batch_op.create_foreign_key(
                "fk_ledger_entries_target_entry_id",
                "ledger_entries",
                ["target_entry_id"],
                ["id"],
                ondelete="SET NULL",
            )


def downgrade() -> None:
    with op.batch_alter_table("ledger_entries") as batch_op:
        batch_op.drop_index("ix_ledger_entries_target_entry_id")
        batch_op.drop_index("ix_ledger_entries_transfer_id")
        batch_op.drop_index("ix_ledger_entries_entry_type")
        batch_op.drop_column("target_entry_id")
        batch_op.drop_column("transfer_id")
        batch_op.drop_column("entry_type")

    with op.batch_alter_table("donations") as batch_op:
        batch_op.drop_index("ix_donations_stripe_subscription_id")
        batch_op.drop_column("retention_expires_at")
        batch_op.drop_column("donor_phone")
        batch_op.alter_column("donor_email", existing_type=sa.String(length=254), nullable=False)
