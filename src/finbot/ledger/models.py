"""SQLAlchemy ORM models for the FinBot database.

Maps the schema defined in docs/design.md §6 to SQLAlchemy 2.x declarative
models.  All tables use UUID primary keys, TIMESTAMPTZ timestamps, and
DECIMAL for monetary values.
"""

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    ARRAY,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.sql import func


class Base(DeclarativeBase):
    """Shared declarative base for all FinBot models."""


# ── Core tables ───────────────────────────────────────────────────────────────


class RawInput(Base):
    """Immutable raw user messages — never modified or deleted."""

    __tablename__ = "raw_inputs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    raw_text: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    # relationship
    ledger_entries: Mapped[list["LedgerEntry"]] = relationship(
        back_populates="raw_input", cascade="all, delete-orphan"
    )


class LedgerEntry(Base):
    """Append-only financial ledger (expense / settlement / correction)."""

    __tablename__ = "ledger"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    raw_input_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_inputs.id"), nullable=False
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="ILS")
    category: Mapped[str | None] = mapped_column(Text, nullable=True)
    payer_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    split_payer_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    split_other_pct: Mapped[Decimal] = mapped_column(Numeric(5, 2), nullable=False)
    event_date: Mapped[date] = mapped_column(Date, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(ARRAY(Text), nullable=True)
    interpretation_version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("ledger.id"), nullable=True
    )

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('expense', 'settlement', 'correction')",
            name="ck_ledger_event_type",
        ),
    )

    # relationships
    raw_input: Mapped["RawInput"] = relationship(back_populates="ledger_entries")
    superseding_entry: Mapped["LedgerEntry | None"] = relationship(
        remote_side="LedgerEntry.id",
        foreign_keys=[superseded_by],
    )


class Category(Base):
    """User-extensible expense categories."""

    __tablename__ = "categories"

    name: Mapped[str] = mapped_column(Text, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class CategoryAlias(Base):
    """Persistent mapping from a pre-approved label to a canonical category.

    E.g. label='internet' → category='utilities'. Used to normalize parsed
    or user-entered labels (e.g. "internet") to a category (e.g. "utilities")
    for display and storage.
    """

    __tablename__ = "category_aliases"

    label: Mapped[str] = mapped_column(Text, primary_key=True)
    category: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class Partnership(Base):
    """Links two Telegram users as finance partners."""

    __tablename__ = "partnerships"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_a_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_b_telegram_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    default_currency: Mapped[str] = mapped_column(Text, nullable=False, server_default="ILS")
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint(
            "user_a_telegram_id",
            "user_b_telegram_id",
            name="uq_partnership_users",
        ),
    )


# ── Observability tables ──────────────────────────────────────────────────────


class LLMCall(Base):
    """Logging for every LLM invocation (local and paid fallback)."""

    __tablename__ = "llm_calls"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_fallback: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default="false")
    fallback_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(8, 6), nullable=True)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())


class FailureLog(Base):
    """Persisted failure log for post-mortem debugging.

    Every caught exception that results in a user-facing error reply is
    recorded here with the original input, the reply sent, the full
    traceback, and a short source label for filtering.
    """

    __tablename__ = "failure_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_input: Mapped[str] = mapped_column(Text, nullable=False)
    error_reply: Mapped[str] = mapped_column(Text, nullable=False)
    traceback: Mapped[str] = mapped_column(Text, nullable=False)
    failure_source: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(nullable=False, server_default=func.now())
