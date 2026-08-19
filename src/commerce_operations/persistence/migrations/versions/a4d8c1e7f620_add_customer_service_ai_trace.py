"""Add customer service AI decision trace.

Revision ID: a4d8c1e7f620
Revises: 9c2a7e4d1b53
Create Date: 2026-08-15
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "a4d8c1e7f620"
down_revision: str | None = "9c2a7e4d1b53"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("customer_messages", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column(
                "intent",
                sa.Enum(
                    "product_enquiry",
                    "order_status",
                    "delivery_question",
                    "return_request",
                    "refund_enquiry",
                    "complaint",
                    "common_marketplace_message",
                    "unknown",
                    name="customer_intent",
                    native_enum=False,
                ),
                nullable=True,
            )
        )
        batch_op.add_column(
            sa.Column(
                "classification",
                sa.Enum(
                    "AUTO_RESPOND",
                    "DRAFT_FOR_APPROVAL",
                    "HUMAN_ESCALATION",
                    name="customer_service_decision",
                    native_enum=False,
                ),
                nullable=True,
            )
        )
        batch_op.add_column(sa.Column("risk_level", sa.String(length=50), nullable=True))
        batch_op.add_column(
            sa.Column("risk_reasons", sa.JSON(), server_default="[]", nullable=False)
        )
        batch_op.add_column(sa.Column("generated_response", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("final_response", sa.Text(), nullable=True))
        batch_op.add_column(sa.Column("structured_ai_response", sa.JSON(), nullable=True))
        batch_op.add_column(sa.Column("prompt_version", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("ai_provider", sa.String(length=100), nullable=True))
        batch_op.add_column(sa.Column("ai_model", sa.String(length=255), nullable=True))
        batch_op.add_column(
            sa.Column("input_tokens", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column("output_tokens", sa.Integer(), server_default="0", nullable=False)
        )
        batch_op.add_column(
            sa.Column(
                "ai_cost_amount",
                sa.Numeric(precision=19, scale=4),
                server_default="0",
                nullable=False,
            )
        )
        batch_op.add_column(sa.Column("ai_cost_currency", sa.String(length=3), nullable=True))
        batch_op.add_column(sa.Column("responded_at", sa.DateTime(timezone=True), nullable=True))
        batch_op.create_index(op.f("ix_customer_messages_intent"), ["intent"])
        batch_op.create_index(op.f("ix_customer_messages_classification"), ["classification"])


def downgrade() -> None:
    with op.batch_alter_table("customer_messages", schema=None) as batch_op:
        batch_op.drop_index(op.f("ix_customer_messages_classification"))
        batch_op.drop_index(op.f("ix_customer_messages_intent"))
        for name in (
            "responded_at",
            "ai_cost_currency",
            "ai_cost_amount",
            "output_tokens",
            "input_tokens",
            "ai_model",
            "ai_provider",
            "prompt_version",
            "structured_ai_response",
            "final_response",
            "generated_response",
            "risk_reasons",
            "risk_level",
            "classification",
            "intent",
        ):
            batch_op.drop_column(name)
