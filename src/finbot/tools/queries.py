"""Query tools for the LLM agent.

Provides read-only tools that the LLM can call to answer user questions
about balances, expenses, and recent activity:

- ``get_balance`` — derive the current balance between partners
- ``query_expenses`` — filter and aggregate expenses
- ``get_recent_entries`` — fetch recent ledger entries
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.ledger.balance import get_balance as _derive_balance
from finbot.ledger.repository import (
    get_category_totals,
    get_filtered_entries,
    get_partnership,
    get_partner_id,
    get_recent_entries as _fetch_recent,
)
from finbot.tools.registry import default_registry


@default_registry.tool(
    name="get_balance",
    description=(
        "Get the current balance between the two partners. "
        "Returns who owes whom and the amount."
    ),
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def get_balance(
    *,
    session: AsyncSession | None = None,
    user_id: int | None = None,
) -> dict:
    """Derive the current balance between partners.

    Args:
        session: Async database session.
        user_id: Telegram user ID of the requesting user.

    Returns:
        A dict with ``balance``, ``currency``, ``who_owes``, and
        ``formatted`` keys.
    """
    if session is None or user_id is None:
        return {
            "error": "Balance check requires a database session and user context.",
        }

    partnership = await get_partnership(session, user_id)
    if partnership is None:
        return {
            "error": "No partnership found. Please set up a partnership first.",
        }

    partner_id = get_partner_id(partnership, user_id)
    balance = await _derive_balance(session, user_id, partner_id)
    currency = partnership.default_currency

    # Balance convention: positive = partner owes user.
    if balance > 0:
        who_owes = "partner_owes_user"
        description = f"Your partner owes you {currency} {abs(balance)}"
    elif balance < 0:
        who_owes = "user_owes_partner"
        description = f"You owe your partner {currency} {abs(balance)}"
    else:
        who_owes = "settled"
        description = "You're all settled up! No outstanding balance."

    return {
        "balance": str(balance),
        "currency": currency,
        "who_owes": who_owes,
        "description": description,
    }


@default_registry.tool(
    name="query_expenses",
    description=(
        "Query and aggregate expenses by optional filters: category, "
        "date range, event type. Returns totals and optional grouped "
        "summaries."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Filter by expense category (e.g. 'groceries').",
            },
            "date_from": {
                "type": "string",
                "description": "Start date in YYYY-MM-DD format (inclusive).",
            },
            "date_to": {
                "type": "string",
                "description": "End date in YYYY-MM-DD format (inclusive).",
            },
            "event_type": {
                "type": "string",
                "enum": ["expense", "settlement", "correction"],
                "description": "Filter by event type.",
            },
            "group_by": {
                "type": "string",
                "enum": ["category"],
                "description": "Group totals by category.",
            },
        },
        "required": [],
    },
)
async def query_expenses(
    *,
    category: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    event_type: str | None = None,
    group_by: str | None = None,
    session: AsyncSession | None = None,
    user_id: int | None = None,
) -> dict:
    """Filter and aggregate ledger entries.

    Args:
        category: Filter by expense category.
        date_from: Start date (YYYY-MM-DD).
        date_to: End date (YYYY-MM-DD).
        event_type: Filter by event type.
        session: Async database session.
        user_id: Telegram user ID of the requesting user.

    Returns:
        A dict with ``total``, ``count``, ``currency``, and either
        ``entries`` or grouped ``categories``.
    """
    if session is None or user_id is None:
        return {"error": "Query requires a database session and user context."}

    partnership = await get_partnership(session, user_id)
    if partnership is None:
        return {"error": "No partnership found."}

    partner_id = get_partner_id(partnership, user_id)

    # Parse date strings.
    parsed_from = _parse_date(date_from)
    parsed_to = _parse_date(date_to)

    if group_by == "category":
        rows = await get_category_totals(
            session,
            user_id,
            partner_id,
            category=category,
            date_from=parsed_from,
            date_to=parsed_to,
            event_type=event_type,
        )
        categories = [
            {"category": cat, "total": str(total), "count": count}
            for cat, total, count in rows
        ]
        total = sum(Decimal(str(item["total"])) for item in categories)
        currency = partnership.default_currency

        parts: list[str] = []
        if date_from:
            parts.append(f"from: {date_from}")
        if date_to:
            parts.append(f"to: {date_to}")
        if event_type:
            parts.append(f"type: {event_type}")
        filter_desc = ", ".join(parts) if parts else "all entries"

        entry_count = sum(item["count"] for item in categories)
        description = (
            f"Found {entry_count} entries ({filter_desc}) "
            f"totalling {currency} {total}."
        )

        return {
            "total": str(total),
            "count": entry_count,
            "currency": currency,
            "categories": categories,
            "entries": [],
            "description": description,
        }

    entries = await get_filtered_entries(
        session,
        user_id,
        partner_id,
        category=category,
        date_from=parsed_from,
        date_to=parsed_to,
        event_type=event_type,
    )

    total = sum(e.amount for e in entries)
    currency = partnership.default_currency

    # Build a concise summary of each entry.
    entry_summaries = []
    for e in entries:
        label = e.description or e.category or e.event_type
        payer_label = "you" if e.payer_telegram_id == user_id else "partner"
        entry_summaries.append({
            "date": str(e.event_date),
            "type": e.event_type,
            "amount": str(e.amount),
            "currency": e.currency,
            "category": e.category,
            "description": label,
            "payer": payer_label,
        })

    # Build description.
    parts: list[str] = []
    if category:
        parts.append(f"category: {category}")
    if date_from:
        parts.append(f"from: {date_from}")
    if date_to:
        parts.append(f"to: {date_to}")
    filter_desc = ", ".join(parts) if parts else "all entries"

    description = (
        f"Found {len(entries)} entries ({filter_desc}) "
        f"totalling {currency} {total}."
    )

    return {
        "total": str(total),
        "count": len(entries),
        "currency": currency,
        "entries": entry_summaries,
        "description": description,
    }


@default_registry.tool(
    name="get_recent_entries",
    description=(
        "Get the most recent ledger entries (expenses, settlements, corrections). "
        "Useful for showing recent activity or providing context."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Maximum number of entries to return (default 10).",
                "default": 10,
            },
        },
        "required": [],
    },
)
async def get_recent_entries(
    *,
    limit: int = 10,
    session: AsyncSession | None = None,
    user_id: int | None = None,
) -> dict:
    """Fetch the most recent active ledger entries.

    Args:
        limit: Maximum entries to return.
        session: Async database session.
        user_id: Telegram user ID of the requesting user.

    Returns:
        A dict with ``count`` and ``entries`` keys.
    """
    if session is None or user_id is None:
        return {"error": "Query requires a database session and user context."}

    partnership = await get_partnership(session, user_id)
    if partnership is None:
        return {"error": "No partnership found."}

    partner_id = get_partner_id(partnership, user_id)
    entries = await _fetch_recent(session, user_id, partner_id, limit=limit)

    entry_summaries = []
    for e in entries:
        label = e.description or e.category or e.event_type
        payer_label = "you" if e.payer_telegram_id == user_id else "partner"
        entry_summaries.append({
            "date": str(e.event_date),
            "type": e.event_type,
            "amount": str(e.amount),
            "currency": e.currency,
            "category": e.category,
            "description": label,
            "payer": payer_label,
        })

    return {
        "count": len(entry_summaries),
        "entries": entry_summaries,
    }


# ── Helpers ───────────────────────────────────────────────────────────────────


def _parse_date(date_str: str | None) -> date | None:
    """Parse a YYYY-MM-DD string into a :class:`date`, or return ``None``."""
    if not date_str:
        return None
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return None
