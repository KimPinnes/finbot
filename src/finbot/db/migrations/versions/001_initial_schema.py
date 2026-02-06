"""Initial schema — all core and observability tables.

Revision ID: 001
Revises: None
Create Date: 2025-12-01

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ── raw_inputs ────────────────────────────────────────────────────
    op.create_table(
        "raw_inputs",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("telegram_user_id", sa.BigInteger, nullable=False),
        sa.Column("raw_text", sa.Text, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── ledger ────────────────────────────────────────────────────────
    op.create_table(
        "ledger",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "raw_input_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("raw_inputs.id"),
            nullable=False,
        ),
        sa.Column("event_type", sa.Text, nullable=False),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("currency", sa.Text, nullable=False, server_default="ILS"),
        sa.Column("category", sa.Text, nullable=True),
        sa.Column("payer_telegram_id", sa.BigInteger, nullable=False),
        sa.Column("split_payer_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("split_other_pct", sa.Numeric(5, 2), nullable=False),
        sa.Column("event_date", sa.Date, nullable=False),
        sa.Column("description", sa.Text, nullable=True),
        sa.Column("tags", postgresql.ARRAY(sa.Text), nullable=True),
        sa.Column(
            "interpretation_version",
            sa.Integer,
            nullable=False,
            server_default="1",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "superseded_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("ledger.id"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "event_type IN ('expense', 'settlement', 'correction')",
            name="ck_ledger_event_type",
        ),
    )

    # ── categories ────────────────────────────────────────────────────
    op.create_table(
        "categories",
        sa.Column("name", sa.Text, primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    # ── partnerships ──────────────────────────────────────────────────
    op.create_table(
        "partnerships",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_a_telegram_id", sa.BigInteger, nullable=False),
        sa.Column("user_b_telegram_id", sa.BigInteger, nullable=False),
        sa.Column("default_currency", sa.Text, nullable=False, server_default="ILS"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "user_a_telegram_id",
            "user_b_telegram_id",
            name="uq_partnership_users",
        ),
    )

    # ── llm_calls (observability) ─────────────────────────────────────
    op.create_table(
        "llm_calls",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("provider", sa.Text, nullable=False),
        sa.Column("model", sa.Text, nullable=False),
        sa.Column("input_tokens", sa.Integer, nullable=True),
        sa.Column("output_tokens", sa.Integer, nullable=True),
        sa.Column("latency_ms", sa.Integer, nullable=True),
        sa.Column(
            "is_fallback",
            sa.Boolean,
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("fallback_reason", sa.Text, nullable=True),
        sa.Column("cost_usd", sa.Numeric(8, 6), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_table("llm_calls")
    op.drop_table("partnerships")
    op.drop_table("categories")
    op.drop_table("ledger")
    op.drop_table("raw_inputs")
