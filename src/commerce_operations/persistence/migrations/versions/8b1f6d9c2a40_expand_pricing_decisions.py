"""Expand deterministic pricing decision data.

Revision ID: 8b1f6d9c2a40
Revises: 3dd83ccaa4b4
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "8b1f6d9c2a40"
down_revision: str | None = "3dd83ccaa4b4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("pricing_decisions", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "fulfilment_costs",
                sa.Numeric(precision=19, scale=4),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column(
                "minimum_margin",
                sa.Numeric(precision=9, scale=4),
                server_default="0",
                nullable=False,
            )
        )
        for name in ("gross_profit", "contribution_profit", "recommended_price"):
            batch_op.add_column(
                sa.Column(
                    name,
                    sa.Numeric(precision=19, scale=4),
                    server_default="0",
                    nullable=False,
                )
            )
        batch_op.add_column(
            sa.Column(
                "margin_percentage",
                sa.Numeric(precision=9, scale=4),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("roi_percentage", sa.Numeric(precision=9, scale=4), nullable=True)
        )
        batch_op.add_column(
            sa.Column("price_change_percentage", sa.Numeric(precision=9, scale=4), nullable=True)
        )
        batch_op.add_column(
            sa.Column(
                "landed_cost_source",
                sa.String(length=50),
                server_default="legacy",
                nullable=False,
            )
        )
        batch_op.add_column(
            sa.Column("commercial_rules", sa.JSON(), server_default="{}", nullable=False)
        )
        batch_op.create_check_constraint(
            op.f("ck_pricing_decisions_nonnegative_fulfilment_costs"),
            "fulfilment_costs >= 0",
        )
        batch_op.create_check_constraint(
            op.f("ck_pricing_decisions_valid_minimum_margin"),
            "minimum_margin >= 0 AND minimum_margin < 1",
        )
        batch_op.create_check_constraint(
            op.f("ck_pricing_decisions_valid_target_margin"),
            "target_margin >= minimum_margin AND target_margin < 1",
        )


def downgrade() -> None:
    with op.batch_alter_table("pricing_decisions", schema=None) as batch_op:
        batch_op.drop_constraint(op.f("ck_pricing_decisions_valid_target_margin"), type_="check")
        batch_op.drop_constraint(op.f("ck_pricing_decisions_valid_minimum_margin"), type_="check")
        batch_op.drop_constraint(
            op.f("ck_pricing_decisions_nonnegative_fulfilment_costs"), type_="check"
        )
        for name in (
            "commercial_rules",
            "landed_cost_source",
            "price_change_percentage",
            "roi_percentage",
            "margin_percentage",
            "recommended_price",
            "contribution_profit",
            "gross_profit",
            "minimum_margin",
            "fulfilment_costs",
        ):
            batch_op.drop_column(name)
