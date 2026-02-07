"""Add failure_log table for post-mortem debugging.

Revision ID: 002
Revises: 001
Create Date: 2025-12-15

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: str | None = "001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "failure_log",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=False),
        sa.Column("user_input", sa.Text, nullable=False),
        sa.Column("error_reply", sa.Text, nullable=False),
        sa.Column("traceback", sa.Text, nullable=False),
        sa.Column("failure_source", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("failure_log")
