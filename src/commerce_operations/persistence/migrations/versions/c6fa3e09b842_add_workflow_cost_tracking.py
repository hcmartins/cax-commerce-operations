"""Add workflow cost tracking.

Revision ID: c6fa3e09b842
Revises: b5e9d2f8a731
"""

import sqlalchemy as sa
from alembic import op

revision = "c6fa3e09b842"
down_revision = "b5e9d2f8a731"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "cost_amount",
                sa.Numeric(precision=19, scale=4),
                nullable=False,
                server_default="0",
            )
        )
        batch_op.add_column(sa.Column("cost_currency", sa.String(length=3), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("workflow_runs") as batch_op:
        batch_op.drop_column("cost_currency")
        batch_op.drop_column("cost_amount")
