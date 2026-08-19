"""Establish the migration baseline.

Revision ID: 20260815_0001
Revises:
Create Date: 2026-08-15
"""

revision: str = "20260815_0001"
down_revision: str | None = None
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    """No business tables are introduced in the foundation phase."""


def downgrade() -> None:
    """The empty baseline has nothing to remove."""
