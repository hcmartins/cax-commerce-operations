"""Add workflow runtime controls and reorder recommendations.

Revision ID: b5e9d2f8a731
Revises: a4d8c1e7f620
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "b5e9d2f8a731"
down_revision: str | None = "a4d8c1e7f620"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_runs", schema=None) as batch_op:
        batch_op.add_column(sa.Column("source_event_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("idempotency_key", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("max_attempts", sa.Integer(), server_default="3", nullable=False)
        )
        batch_op.add_column(
            sa.Column("timeout_seconds", sa.Integer(), server_default="300", nullable=False)
        )
        batch_op.add_column(sa.Column("started_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.add_column(sa.Column("waiting_approval_id", sa.Uuid(), nullable=True))
        batch_op.create_index(op.f("ix_workflow_runs_source_event_id"), ["source_event_id"])
        batch_op.create_index(op.f("ix_workflow_runs_deadline_at"), ["deadline_at"])
        batch_op.create_index(op.f("ix_workflow_runs_waiting_approval_id"), ["waiting_approval_id"])
        batch_op.create_unique_constraint(
            op.f("uq_workflow_runs_idempotency_key"), ["idempotency_key"]
        )
    op.create_table(
        "reorder_recommendations",
        sa.Column("inventory_item_id", sa.Uuid(), nullable=False),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=False),
        sa.Column("source_event_id", sa.Uuid(), nullable=False),
        sa.Column("available_quantity", sa.Integer(), nullable=False),
        sa.Column("low_stock_threshold", sa.Integer(), nullable=False),
        sa.Column("suggested_quantity", sa.Integer(), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=50), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.CheckConstraint(
            "available_quantity >= 0",
            name=op.f("ck_reorder_recommendations_nonnegative_available_quantity"),
        ),
        sa.CheckConstraint(
            "low_stock_threshold >= 0",
            name=op.f("ck_reorder_recommendations_nonnegative_reorder_threshold"),
        ),
        sa.CheckConstraint(
            "suggested_quantity > 0",
            name=op.f("ck_reorder_recommendations_positive_suggested_quantity"),
        ),
        sa.ForeignKeyConstraint(
            ["inventory_item_id"],
            ["inventory_items.id"],
            name=op.f("fk_reorder_recommendations_inventory_item_id_inventory_items"),
        ),
        sa.ForeignKeyConstraint(
            ["workflow_run_id"],
            ["workflow_runs.id"],
            name=op.f("fk_reorder_recommendations_workflow_run_id_workflow_runs"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reorder_recommendations")),
        sa.UniqueConstraint(
            "source_event_id", name=op.f("uq_reorder_recommendations_source_event_id")
        ),
    )
    op.create_index(
        op.f("ix_reorder_recommendations_inventory_item_id"),
        "reorder_recommendations",
        ["inventory_item_id"],
    )
    op.create_index(
        op.f("ix_reorder_recommendations_workflow_run_id"),
        "reorder_recommendations",
        ["workflow_run_id"],
    )
    op.create_index(
        op.f("ix_reorder_recommendations_status"),
        "reorder_recommendations",
        ["status"],
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_reorder_recommendations_status"), table_name="reorder_recommendations")
    op.drop_index(
        op.f("ix_reorder_recommendations_workflow_run_id"),
        table_name="reorder_recommendations",
    )
    op.drop_index(
        op.f("ix_reorder_recommendations_inventory_item_id"),
        table_name="reorder_recommendations",
    )
    op.drop_table("reorder_recommendations")
    with op.batch_alter_table("workflow_runs", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("uq_workflow_runs_idempotency_key"), type_="unique")
        batch_op.drop_index(op.f("ix_workflow_runs_waiting_approval_id"))
        batch_op.drop_index(op.f("ix_workflow_runs_deadline_at"))
        batch_op.drop_index(op.f("ix_workflow_runs_source_event_id"))
        for name in (
            "waiting_approval_id",
            "completed_at",
            "deadline_at",
            "started_at",
            "timeout_seconds",
            "max_attempts",
            "idempotency_key",
            "source_event_id",
        ):
            batch_op.drop_column(name)
