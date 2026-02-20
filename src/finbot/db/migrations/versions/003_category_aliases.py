"""Add category_aliases table for persistent label → category mapping.

Revision ID: 003
Revises: 002
Create Date: 2026-02-10

Stores pre-approved mappings (e.g. internet → utilities) so labels
are normalized to a canonical category without code changes.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.sql import column, table

# revision identifiers, used by Alembic.
revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Default mappings: label (e.g. "internet") → category (e.g. "utilities").
_DEFAULT_ALIASES: list[tuple[str, str]] = [
    ("broadband", "utilities"),
    ("electric", "utilities"),
    ("electricity", "utilities"),
    ("heating", "utilities"),
    ("internet", "utilities"),
    ("phone", "utilities"),
    ("sewage", "utilities"),
    ("trash", "utilities"),
    ("water", "utilities"),
]


def upgrade() -> None:
    op.create_table(
        "category_aliases",
        sa.Column("label", sa.Text, primary_key=True),
        sa.Column("category", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    # Seed default label → category mappings.
    aliases_table = table(
        "category_aliases",
        column("label", sa.Text),
        column("category", sa.Text),
    )
    op.bulk_insert(
        aliases_table,
        [{"label": label, "category": category} for label, category in _DEFAULT_ALIASES],
    )


def downgrade() -> None:
    op.drop_table("category_aliases")
