"""Initial schema for Batack API

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-15 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0001_initial"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

JSON_TYPE = sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql")


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("full_name", sa.String(length=120), nullable=False),
        sa.Column(
            "role",
            sa.Enum("admin", "tresorier", "membre", "encadrant", name="user_role"),
            nullable=False,
        ),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("must_change_password", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("totp_secret", sa.String(length=64), nullable=True),
        sa.Column("totp_enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("failed_login_attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("locked_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    op.create_table(
        "user_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("token_hash", sa.String(length=64), nullable=False),
        sa.Column("refresh_token_hash", sa.String(length=64), nullable=True),
        sa.Column("user_agent", sa.String(length=512), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("is_revoked", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("refresh_token_hash", name="uq_user_sessions_refresh"),
    )
    op.create_index("ix_user_sessions_user_id", "user_sessions", ["user_id"])
    op.create_index("ix_user_sessions_token_hash", "user_sessions", ["token_hash"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("actor_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("actor_email", sa.String(length=254), nullable=True),
        sa.Column("actor_role", sa.String(length=32), nullable=True),
        sa.Column("action", sa.String(length=100), nullable=False),
        sa.Column("resource_type", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=64), nullable=True),
        sa.Column("ip_address", sa.String(length=64), nullable=True),
        sa.Column("details", JSON_TYPE, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_audit_logs_actor_id", "audit_logs", ["actor_id"])
    op.create_index("ix_audit_logs_action", "audit_logs", ["action"])
    op.create_index("ix_audit_logs_resource_type", "audit_logs", ["resource_type"])
    op.create_index("ix_audit_logs_created_at", "audit_logs", ["created_at"])

    op.create_table(
        "media_assets",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=120), nullable=True),
        sa.Column(
            "category",
            sa.Enum("photo", "logo", "justificatif", "document", "autre", name="media_category"),
            nullable=False,
        ),
        sa.Column("filename", sa.String(length=255), nullable=False),
        sa.Column("original_filename", sa.String(length=255), nullable=False),
        sa.Column("storage_path", sa.String(length=512), nullable=False, unique=True),
        sa.Column("mime_type", sa.String(length=100), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("width", sa.Integer(), nullable=True),
        sa.Column("height", sa.Integer(), nullable=True),
        sa.Column("alt_texts", JSON_TYPE, nullable=False),
        sa.Column("captions", JSON_TYPE, nullable=False),
        sa.Column("is_public", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("uploaded_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_media_assets_slug", "media_assets", ["slug"], unique=True)
    op.create_index("ix_media_assets_category", "media_assets", ["category"])
    op.create_index("ix_media_assets_sha256", "media_assets", ["sha256"])

    op.create_table(
        "projects",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("category", JSON_TYPE, nullable=False),
        sa.Column("title", JSON_TYPE, nullable=False),
        sa.Column("tagline", JSON_TYPE, nullable=False),
        sa.Column("description", JSON_TYPE, nullable=False),
        sa.Column("photo_id", sa.String(length=36), sa.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("photo_slug", sa.String(length=120), nullable=True),
        sa.Column("target_amount_eur", sa.Numeric(15, 2), nullable=False, server_default="0.00"),
        sa.Column("collected_amount_eur", sa.Numeric(15, 2), nullable=False, server_default="0.00"),
        sa.Column("contributors_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("is_featured_need", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("display_order", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "status",
            sa.Enum("active", "completed", "archived", name="project_status"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_projects_slug", "projects", ["slug"], unique=True)

    op.create_table(
        "site_contents",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("key", sa.String(length=120), nullable=False),
        sa.Column("section", sa.String(length=64), nullable=False),
        sa.Column("content", JSON_TYPE, nullable=False),
        sa.Column("media_id", sa.String(length=36), sa.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("updated_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_site_contents_key", "site_contents", ["key"], unique=True)
    op.create_index("ix_site_contents_section", "site_contents", ["section"])

    op.create_table(
        "project_subscriptions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="fr"),
        sa.Column("consent_given", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("consent_date", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "status",
            sa.Enum("pending", "active", "unsubscribed", name="subscription_status"),
            nullable=False,
        ),
        sa.Column("confirm_token_hash", sa.String(length=64), nullable=True, unique=True),
        sa.Column("unsubscribe_token_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("email", "project_id", name="uq_subscription_email_project"),
    )
    op.create_index("ix_project_subscriptions_email", "project_subscriptions", ["email"])
    op.create_index("ix_project_subscriptions_project_id", "project_subscriptions", ["project_id"])

    currency_enum = sa.Enum("EUR", "FCFA", name="currency_enum")

    op.create_table(
        "donations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("amount_eur", sa.Numeric(15, 2), nullable=False),
        sa.Column("original_amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", currency_enum, nullable=False),
        sa.Column(
            "frequency",
            sa.Enum("ponctuel", "mensuel", name="donation_frequency"),
            nullable=False,
        ),
        sa.Column(
            "payment_method",
            sa.Enum("carte", "bancontact", "mobile_money", "especes", "virement", name="payment_method"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum("pending", "paid", "failed", "cancelled", "refunded", name="donation_status"),
            nullable=False,
        ),
        sa.Column("donor_first_name", sa.String(length=100), nullable=False, server_default=""),
        sa.Column("donor_last_name", sa.String(length=100), nullable=False),
        sa.Column("donor_email", sa.String(length=254), nullable=False),
        sa.Column("tax_receipt_requested", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("terms_accepted", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("language", sa.String(length=8), nullable=False, server_default="fr"),
        sa.Column("stripe_customer_id", sa.String(length=120), nullable=True),
        sa.Column("stripe_session_id", sa.String(length=120), nullable=True),
        sa.Column("stripe_payment_intent_id", sa.String(length=120), nullable=True),
        sa.Column("stripe_subscription_id", sa.String(length=120), nullable=True),
        sa.Column("receipt_reference", sa.String(length=120), nullable=True),
        sa.Column("recorded_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_donations_project_id", "donations", ["project_id"])
    op.create_index("ix_donations_status", "donations", ["status"])
    op.create_index("ix_donations_donor_email", "donations", ["donor_email"])
    op.create_index("ix_donations_stripe_session_id", "donations", ["stripe_session_id"], unique=True)

    op.create_table(
        "stripe_events",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("event_id", sa.String(length=120), nullable=False),
        sa.Column("event_type", sa.String(length=120), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_stripe_events_event_id", "stripe_events", ["event_id"], unique=True)

    op.create_table(
        "ledger_entries",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("entry_number", sa.Integer(), nullable=False),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("label", sa.String(length=255), nullable=False),
        sa.Column("account", sa.String(length=120), nullable=False),
        sa.Column(
            "direction",
            sa.Enum("entree", "sortie", name="ledger_direction"),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", currency_enum, nullable=False),
        sa.Column("amount_eur_equiv", sa.Numeric(15, 2), nullable=False),
        sa.Column("amount_fcfa_equiv", sa.BigInteger(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("a_contresigner", "validee", "rejetee", name="ledger_status"),
            nullable=False,
        ),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("donation_id", sa.String(length=36), sa.ForeignKey("donations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("justificatif_media_id", sa.String(length=36), sa.ForeignKey("media_assets.id", ondelete="SET NULL"), nullable=True),
        sa.Column("receipt_number", sa.String(length=120), nullable=True),
        sa.Column("corrects_entry_id", sa.String(length=36), sa.ForeignKey("ledger_entries.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("validated_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.Text(), nullable=True),
        sa.Column("previous_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_ledger_entries_entry_number", "ledger_entries", ["entry_number"], unique=True)
    op.create_index("ix_ledger_entries_entry_date", "ledger_entries", ["entry_date"])
    op.create_index("ix_ledger_entries_account", "ledger_entries", ["account"])
    op.create_index("ix_ledger_entries_status", "ledger_entries", ["status"])
    op.create_index("ix_ledger_entries_project_id", "ledger_entries", ["project_id"])

    op.create_table(
        "fund_transfers",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("source_project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("destination_project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_account", sa.String(length=120), nullable=False),
        sa.Column("destination_account", sa.String(length=120), nullable=False),
        sa.Column("amount", sa.Numeric(15, 2), nullable=False),
        sa.Column("currency", currency_enum, nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("is_repaid", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("repaid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("transfer_date", sa.Date(), nullable=False),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "project_tasks",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("code_id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.String(length=36), sa.ForeignKey("projects.id", ondelete="SET NULL"), nullable=True),
        sa.Column("label", sa.String(length=200), nullable=False),
        sa.Column("cost_eur", sa.Numeric(15, 2), nullable=False),
        sa.Column("duration_months", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("weight", sa.Numeric(12, 4), nullable=False, server_default="1.0000"),
        sa.Column(
            "status",
            sa.Enum("candidate", "planned", "in_progress", "completed", name="task_status"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_project_tasks_code_id", "project_tasks", ["code_id"], unique=True)
    op.create_index("ix_project_tasks_project_id", "project_tasks", ["project_id"])

    op.create_table(
        "task_precedences",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("predecessor_id", sa.String(length=36), sa.ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.Column("successor_id", sa.String(length=36), sa.ForeignKey("project_tasks.id", ondelete="CASCADE"), nullable=False),
        sa.UniqueConstraint("predecessor_id", "successor_id", name="uq_task_precedence"),
    )

    op.create_table(
        "ahp_sessions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("criteria", JSON_TYPE, nullable=False),
        sa.Column("comparison_matrix", JSON_TYPE, nullable=False),
        sa.Column("criteria_weights", JSON_TYPE, nullable=False),
        sa.Column("consistency_ratio", sa.Numeric(8, 5), nullable=False),
        sa.Column("is_consistent", sa.Boolean(), nullable=False),
        sa.Column("task_scores", JSON_TYPE, nullable=False),
        sa.Column("applied_to_tasks", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "investment_plans",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("name", sa.String(length=160), nullable=False),
        sa.Column("instance_data", JSON_TYPE, nullable=False),
        sa.Column("solver_result", JSON_TYPE, nullable=False),
        sa.Column(
            "status",
            sa.Enum("draft", "validated", "archived", name="plan_status"),
            nullable=False,
        ),
        sa.Column("created_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("validated_by_id", sa.String(length=36), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_investment_plans_status", "investment_plans", ["status"])


def downgrade() -> None:
    op.drop_table("investment_plans")
    op.drop_table("ahp_sessions")
    op.drop_table("task_precedences")
    op.drop_table("project_tasks")
    op.drop_table("fund_transfers")
    op.drop_table("ledger_entries")
    op.drop_table("stripe_events")
    op.drop_table("donations")
    op.drop_table("project_subscriptions")
    op.drop_table("site_contents")
    op.drop_table("projects")
    op.drop_table("media_assets")
    op.drop_table("audit_logs")
    op.drop_table("user_sessions")
    op.drop_table("users")

