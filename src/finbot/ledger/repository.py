"""Database repository for ledger operations.

Provides async functions for persisting and querying financial data.
Phase 2 implements raw_input storage; Phase 3 adds LLM call logging.
Phase 4 adds ledger entry persistence for committed expenses.
Phase 5 adds query functions for balance derivation, expense filtering,
and partnership lookup.
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.ledger.models import (
    Category,
    CategoryAlias,
    FailureLog,
    LedgerEntry,
    LLMCall,
    Partnership,
    RawInput,
)


async def save_raw_input(
    session: AsyncSession,
    telegram_user_id: int,
    raw_text: str,
) -> RawInput:
    """Persist a raw user message to the ``raw_inputs`` table.

    Every incoming message is stored immutably before any processing.
    This guarantees an audit trail and enables future reprocessing.

    Args:
        session: Active async database session (caller manages commit).
        telegram_user_id: Telegram user ID of the message sender.
        raw_text: The original message text exactly as received.

    Returns:
        The newly created :class:`RawInput` instance (with ``id`` populated
        after flush).
    """
    raw_input = RawInput(
        telegram_user_id=telegram_user_id,
        raw_text=raw_text,
    )
    session.add(raw_input)
    await session.flush()
    return raw_input


# ── LLM call logging (Phase 3 — ADR-006) ────────────────────────────────────


async def save_llm_call(
    session: AsyncSession,
    *,
    provider: str,
    model: str,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    latency_ms: int | None = None,
    is_fallback: bool = False,
    fallback_reason: str | None = None,
    cost_usd: Decimal | None = None,
) -> LLMCall:
    """Log an LLM invocation to the ``llm_calls`` table.

    Every LLM call — local and paid — is recorded for cost tracking,
    latency monitoring, and fallback frequency analysis (ADR-006).

    Args:
        session: Active async database session (caller manages commit).
        provider: LLM provider name (e.g. ``"ollama"``, ``"anthropic"``).
        model: Model identifier used for the call.
        input_tokens: Number of input/prompt tokens (if available).
        output_tokens: Number of output/completion tokens (if available).
        latency_ms: Wall-clock latency of the call in milliseconds.
        is_fallback: Whether this was a fallback call (local model failed).
        fallback_reason: Reason the local model was bypassed (if fallback).
        cost_usd: Estimated cost in USD for paid API calls.

    Returns:
        The newly created :class:`LLMCall` instance.
    """
    llm_call = LLMCall(
        provider=provider,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        latency_ms=latency_ms,
        is_fallback=is_fallback,
        fallback_reason=fallback_reason,
        cost_usd=cost_usd,
    )
    session.add(llm_call)
    await session.flush()
    return llm_call


# ── Failure logging ──────────────────────────────────────────────────────────


async def save_failure(
    session: AsyncSession,
    *,
    telegram_user_id: int,
    user_input: str,
    error_reply: str,
    traceback_str: str,
    failure_source: str,
) -> FailureLog:
    """Persist a failure record for later debugging.

    Called from orchestrator ``except`` blocks so that every user-visible
    error is captured with full context (input, reply, traceback, source).

    Args:
        session: Active async database session (caller manages commit).
        telegram_user_id: Telegram user ID that triggered the failure.
        user_input: The raw message text that caused the failure.
        error_reply: The error message sent back to the user.
        traceback_str: Full Python traceback as a string.
        failure_source: Short label for the failure site (e.g. ``"llm_parse"``).

    Returns:
        The newly created :class:`FailureLog` instance.
    """
    row = FailureLog(
        telegram_user_id=telegram_user_id,
        user_input=user_input,
        error_reply=error_reply,
        traceback=traceback_str,
        failure_source=failure_source,
    )
    session.add(row)
    await session.flush()
    return row


# ── Ledger entry persistence (Phase 4) ───────────────────────────────────────


async def save_ledger_entry(
    session: AsyncSession,
    *,
    raw_input_id: uuid.UUID,
    event_type: str,
    amount: Decimal,
    currency: str = "ILS",
    category: str | None = None,
    payer_telegram_id: int,
    split_payer_pct: Decimal,
    split_other_pct: Decimal,
    event_date: date,
    description: str | None = None,
    tags: list[str] | None = None,
) -> LedgerEntry:
    """Commit a validated expense/settlement/correction to the ledger.

    This is the final write step in the agent flow — called only after the
    user has confirmed the entry via the inline keyboard.

    Args:
        session: Active async database session (caller manages commit).
        raw_input_id: UUID of the originating ``raw_inputs`` row.
        event_type: One of ``'expense'``, ``'settlement'``, ``'correction'``.
        amount: Monetary amount (positive).
        currency: Three-letter currency code (default ``'ILS'``).
        category: Expense category (e.g. ``'groceries'``).
        payer_telegram_id: Telegram user ID of the payer.
        split_payer_pct: Payer's share as a percentage (0-100).
        split_other_pct: Other partner's share (0-100, sums to 100 with payer).
        event_date: Date the expense/settlement occurred.
        description: Optional freeform description.
        tags: Optional list of tag strings.

    Returns:
        The newly created :class:`LedgerEntry` instance (with ``id``
        populated after flush).
    """
    entry = LedgerEntry(
        raw_input_id=raw_input_id,
        event_type=event_type,
        amount=amount,
        currency=currency,
        category=category,
        payer_telegram_id=payer_telegram_id,
        split_payer_pct=split_payer_pct,
        split_other_pct=split_other_pct,
        event_date=event_date,
        description=description,
        tags=tags,
    )
    session.add(entry)
    await session.flush()
    return entry


# ── Query functions (Phase 5) ────────────────────────────────────────────────


async def get_active_ledger_entries(
    session: AsyncSession,
    user_a_id: int,
    user_b_id: int,
) -> list[LedgerEntry]:
    """Return all active (non-superseded) ledger entries for a partnership.

    Active entries are those where ``superseded_by IS NULL``, involving
    either partner as the payer.

    Args:
        session: Active async database session.
        user_a_id: Telegram user ID of the first partner.
        user_b_id: Telegram user ID of the second partner.

    Returns:
        A list of :class:`LedgerEntry` instances ordered by ``event_date``
        then ``created_at``.
    """
    stmt = (
        select(LedgerEntry)
        .where(
            LedgerEntry.superseded_by.is_(None),
            or_(
                LedgerEntry.payer_telegram_id == user_a_id,
                LedgerEntry.payer_telegram_id == user_b_id,
            ),
        )
        .order_by(LedgerEntry.event_date, LedgerEntry.created_at)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_filtered_entries(
    session: AsyncSession,
    user_a_id: int,
    user_b_id: int,
    *,
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
) -> list[LedgerEntry]:
    """Return active ledger entries matching the given filters.

    Args:
        session: Active async database session.
        user_a_id: Telegram user ID of the first partner.
        user_b_id: Telegram user ID of the second partner.
        category: Filter by expense category (case-insensitive).
        date_from: Include entries on or after this date.
        date_to: Include entries on or before this date.
        event_type: Filter by event type (``'expense'``, ``'settlement'``,
            ``'correction'``).

    Returns:
        A list of matching :class:`LedgerEntry` instances ordered by
        ``event_date`` descending.
    """
    stmt = select(LedgerEntry).where(
        LedgerEntry.superseded_by.is_(None),
        or_(
            LedgerEntry.payer_telegram_id == user_a_id,
            LedgerEntry.payer_telegram_id == user_b_id,
        ),
    )

    if category is not None:
        stmt = stmt.where(LedgerEntry.category.ilike(category))
    if date_from is not None:
        stmt = stmt.where(LedgerEntry.event_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(LedgerEntry.event_date <= date_to)
    if event_type is not None:
        stmt = stmt.where(LedgerEntry.event_type == event_type)

    stmt = stmt.order_by(LedgerEntry.event_date.desc(), LedgerEntry.created_at.desc())

    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_recent_entries(
    session: AsyncSession,
    user_a_id: int,
    user_b_id: int,
    limit: int = 10,
) -> list[LedgerEntry]:
    """Return the most recent active ledger entries for a partnership.

    Args:
        session: Active async database session.
        user_a_id: Telegram user ID of the first partner.
        user_b_id: Telegram user ID of the second partner.
        limit: Maximum number of entries to return (default 10).

    Returns:
        A list of :class:`LedgerEntry` instances ordered by most recent first.
    """
    stmt = (
        select(LedgerEntry)
        .where(
            LedgerEntry.superseded_by.is_(None),
            or_(
                LedgerEntry.payer_telegram_id == user_a_id,
                LedgerEntry.payer_telegram_id == user_b_id,
            ),
        )
        .order_by(LedgerEntry.event_date.desc(), LedgerEntry.created_at.desc())
        .limit(limit)
    )
    result = await session.execute(stmt)
    return list(result.scalars().all())


async def get_category_totals(
    session: AsyncSession,
    user_a_id: int,
    user_b_id: int,
    *,
    category: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    event_type: str | None = None,
) -> list[tuple[str, Decimal, int]]:
    """Return aggregated totals grouped by category.

    Returns list of tuples: (category, total, count).
    """
    stmt = select(
        LedgerEntry.category,
        func.sum(LedgerEntry.amount),
        func.count(LedgerEntry.id),
    ).where(
        LedgerEntry.superseded_by.is_(None),
        or_(
            LedgerEntry.payer_telegram_id == user_a_id,
            LedgerEntry.payer_telegram_id == user_b_id,
        ),
    )

    if category is not None:
        stmt = stmt.where(LedgerEntry.category.ilike(category))
    if date_from is not None:
        stmt = stmt.where(LedgerEntry.event_date >= date_from)
    if date_to is not None:
        stmt = stmt.where(LedgerEntry.event_date <= date_to)
    if event_type is not None:
        stmt = stmt.where(LedgerEntry.event_type == event_type)

    stmt = stmt.group_by(LedgerEntry.category).order_by(func.sum(LedgerEntry.amount).desc())

    result = await session.execute(stmt)
    rows = list(result.all())
    normalized: list[tuple[str, Decimal, int]] = []
    for cat, total, count in rows:
        normalized.append((cat or "uncategorized", total or Decimal("0"), int(count)))
    return normalized


async def get_partnership(
    session: AsyncSession,
    user_id: int,
) -> Partnership | None:
    """Look up the partnership that includes *user_id*.

    Since the system supports exactly one partnership per user (two-partner
    model), this returns the first match where the user appears as either
    ``user_a`` or ``user_b``.

    Args:
        session: Active async database session.
        user_id: Telegram user ID to look up.

    Returns:
        The :class:`Partnership` instance if found, else ``None``.
    """
    stmt = (
        select(Partnership)
        .where(
            or_(
                Partnership.user_a_telegram_id == user_id,
                Partnership.user_b_telegram_id == user_id,
            ),
        )
        .limit(1)
    )
    result = await session.execute(stmt)
    return result.scalar_one_or_none()


def get_partner_id(partnership: Partnership, user_id: int) -> int:
    """Return the *other* partner's Telegram user ID.

    Args:
        partnership: The partnership record.
        user_id: The caller's Telegram user ID.

    Returns:
        The other partner's Telegram user ID.
    """
    if partnership.user_a_telegram_id == user_id:
        return partnership.user_b_telegram_id
    return partnership.user_a_telegram_id


# ── Category aliases (label → category mapping) ─────────────────────────────


async def get_category_aliases(session: AsyncSession) -> dict[str, str]:
    """Load all label → category mappings from the database.

    Returns a dict mapping lowercase label to category (e.g. {"internet": "utilities"}).
    Used to normalize parsed or user-entered labels to a canonical category.
    """
    stmt = select(CategoryAlias.label, CategoryAlias.category)
    result = await session.execute(stmt)
    return {row.label.lower(): row.category.lower() for row in result.all()}


async def get_category_aliases_safe(session: AsyncSession) -> dict[str, str]:
    """Load category aliases inside a savepoint; return {} if the query fails.

    Use this when the category_aliases table may not exist (e.g. migration not
    applied). A failed query aborts only the savepoint, not the outer transaction.
    """
    try:
        async with session.begin_nested():
            return await get_category_aliases(session)
    except Exception:
        return {}


async def ensure_category_alias(
    session: AsyncSession,
    label: str,
    category: str,
) -> None:
    """Insert or replace a label → category mapping. Caller must commit."""
    normalised_label = label.strip().lower()
    normalised_category = category.strip().lower()
    if not normalised_label or not normalised_category:
        return
    alias = CategoryAlias(
        label=normalised_label,
        category=normalised_category,
    )
    await session.merge(alias)
    await session.flush()


# ── Category management ──────────────────────────────────────────────────────


async def save_category(
    session: AsyncSession,
    name: str,
) -> tuple[Category, bool]:
    """Insert a new category or return the existing one.

    The category name is normalised to lowercase before insertion.

    Args:
        session: Active async database session (caller manages commit).
        name: Category name to create (will be lowercased).

    Returns:
        A tuple of ``(category, created)`` where *created* is ``True``
        if a new row was inserted, ``False`` if the category already existed.
    """
    normalised = name.strip().lower()

    # Check for existing category.
    stmt = select(Category).where(Category.name == normalised)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()

    if existing is not None:
        return existing, False

    category = Category(name=normalised)
    session.add(category)
    await session.flush()
    return category, True


async def rename_category(
    session: AsyncSession,
    old_name: str,
    new_name: str,
) -> tuple[bool, int]:
    """Rename a category and update all historical ledger entries.

    Since ``Category.name`` is the primary key, this deletes the old row
    and inserts a new one, then updates every ``ledger`` row that
    referenced the old name.

    Args:
        session: Active async database session (caller manages commit).
        old_name: Current category name (case-insensitive).
        new_name: Desired new category name (will be lowercased).

    Returns:
        A tuple of ``(success, ledger_count)`` where *success* is ``True``
        if the rename was performed, and *ledger_count* is the number of
        ledger entries that were updated.
    """
    from sqlalchemy import update

    old_normalised = old_name.strip().lower()
    new_normalised = new_name.strip().lower()

    if old_normalised == new_normalised:
        return False, 0

    # Verify old category exists.
    stmt = select(Category).where(Category.name == old_normalised)
    result = await session.execute(stmt)
    existing = result.scalar_one_or_none()
    if existing is None:
        return False, 0

    # Check the new name isn't already taken.
    stmt_new = select(Category).where(Category.name == new_normalised)
    result_new = await session.execute(stmt_new)
    if result_new.scalar_one_or_none() is not None:
        return False, 0

    # Delete old category row and insert new one.
    await session.delete(existing)
    await session.flush()
    new_category = Category(name=new_normalised)
    session.add(new_category)
    await session.flush()

    # Update all ledger entries that used the old category name.
    upd = (
        update(LedgerEntry)
        .where(LedgerEntry.category == old_normalised)
        .values(category=new_normalised)
    )
    ledger_result = await session.execute(upd)
    ledger_count = ledger_result.rowcount  # type: ignore[union-attr]

    return True, ledger_count


# ── Partnership management ───────────────────────────────────────────────────


async def save_partnership(
    session: AsyncSession,
    user_a_id: int,
    user_b_id: int,
    default_currency: str = "ILS",
) -> tuple[Partnership, bool]:
    """Create a new partnership between two Telegram users.

    If a partnership already exists for either user, returns the existing
    one without modification.

    Args:
        session: Active async database session (caller manages commit).
        user_a_id: Telegram user ID of the first partner.
        user_b_id: Telegram user ID of the second partner.
        default_currency: Default currency code for the partnership.

    Returns:
        A tuple of ``(partnership, created)`` where *created* is ``True``
        if a new row was inserted, ``False`` if a partnership already existed.
    """
    # Check if either user already has a partnership.
    existing = await get_partnership(session, user_a_id)
    if existing is not None:
        return existing, False

    existing = await get_partnership(session, user_b_id)
    if existing is not None:
        return existing, False

    partnership = Partnership(
        user_a_telegram_id=user_a_id,
        user_b_telegram_id=user_b_id,
        default_currency=default_currency,
    )
    session.add(partnership)
    await session.flush()
    return partnership, True
