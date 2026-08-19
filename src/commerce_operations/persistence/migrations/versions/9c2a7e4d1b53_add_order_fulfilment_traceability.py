"""Add order fulfilment traceability.

Revision ID: 9c2a7e4d1b53
Revises: 8b1f6d9c2a40
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "9c2a7e4d1b53"
down_revision: str | None = "8b1f6d9c2a40"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.add_column(sa.Column("workflow_run_id", sa.Uuid(), nullable=True))
        batch_op.add_column(sa.Column("source_event_id", sa.String(length=255), nullable=True))
        batch_op.add_column(sa.Column("source_payload_hash", sa.String(length=64), nullable=True))
        batch_op.create_foreign_key(
            op.f("fk_orders_workflow_run_id_workflow_runs"),
            "workflow_runs",
            ["workflow_run_id"],
            ["id"],
        )
        batch_op.create_index(op.f("ix_orders_workflow_run_id"), ["workflow_run_id"])
    op.execute(
        "UPDATE orders SET source_event_id = CAST(id AS VARCHAR(36)), "
        "source_payload_hash = 'legacy-' || CAST(id AS VARCHAR(36)) "
        "WHERE source_event_id IS NULL"
    )
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.alter_column("source_event_id", existing_type=sa.String(255), nullable=False)
        batch_op.alter_column("source_payload_hash", existing_type=sa.String(64), nullable=False)
        batch_op.create_unique_constraint(
            op.f("uq_orders_source_event"),
            ["marketplace", "marketplace_account_id", "source_event_id"],
        )


def downgrade() -> None:
    with op.batch_alter_table("orders", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("uq_orders_source_event"), type_="unique")
        batch_op.drop_index(op.f("ix_orders_workflow_run_id"))
        batch_op.drop_constraint(
            op.f("fk_orders_workflow_run_id_workflow_runs"), type_="foreignkey"
        )
        batch_op.drop_column("source_payload_hash")
        batch_op.drop_column("source_event_id")
        batch_op.drop_column("workflow_run_id")
