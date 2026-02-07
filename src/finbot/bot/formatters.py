"""Message formatters for Telegram output.

Converts structured financial data into Telegram-friendly HTML strings.
Phase 2 provided stubs; Phase 4 adds the full confirmation summary used
by the agent orchestrator.  Phase 5 adds query result, settlement
confirmation, and recent entries formatters.
"""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from finbot.agent.state import PendingExpense


def format_expense_summary(expenses: list[dict[str, Any]]) -> str:
    """Format a list of parsed expenses into an HTML summary for Telegram.

    Args:
        expenses: List of expense dicts, each expected to contain keys
            like ``amount``, ``category``, ``payer``, ``split``, ``date``.

    Returns:
        An HTML-formatted string suitable for ``parse_mode=HTML``.
    """
    if not expenses:
        return "<i>No expenses to display.</i>"

    lines: list[str] = []
    for i, exp in enumerate(expenses, 1):
        amount = exp.get("amount", "?")
        currency = exp.get("currency", "ILS")
        category = exp.get("category", "uncategorized")
        description = exp.get("description", "")
        label = description or category
        lines.append(f"{i}. <b>{label}</b> — {currency} {amount}")

    return "\n".join(lines)


# ── Confirmation summary (Phase 4) ───────────────────────────────────────────


def _build_label(
    description: str | None,
    category: str | None,
    known_categories: set[str] | None = None,
) -> str:
    """Build a display label showing both description and category.

    Returns e.g. ``"Water (utilities)"`` when both exist and differ,
    or just one when only one is present.  Appends a ``[NEW]`` marker
    if the category is not in *known_categories*.
    """
    cat_tag = ""
    if category and known_categories is not None and category.lower() not in known_categories:
        cat_tag = " [NEW]"

    if description and category and description.lower() != category.lower():
        return f"{description} ({category}{cat_tag})"
    if description:
        return description
    if category:
        return f"{category}{cat_tag}"
    return "expense"


def format_confirmation_summary(
    expenses: list[PendingExpense],
    known_categories: set[str] | None = None,
) -> str:
    """Format pending expenses into a rich confirmation message.

    Shows all resolved fields including a who-owes-whom calculation,
    matching the design doc example::

        2 expenses:
        1. Groceries ILS 300 — you paid, split 70/30 → partner owes ILS 90
        2. Gas ILS 200 — you paid, split 70/30 → partner owes ILS 60
        Date: 2025-12-05

    Args:
        expenses: List of :class:`PendingExpense` objects (should be complete).
        known_categories: Optional set of known category names. When provided,
            categories not in this set are visually flagged as new.

    Returns:
        An HTML-formatted string suitable for ``parse_mode=HTML``.
    """
    if not expenses:
        return "<i>No expenses to display.</i>"

    count = len(expenses)
    header = f"\U0001f4dd <b>{count} expense{'s' if count != 1 else ''}:</b>\n"
    lines: list[str] = [header]

    for i, exp in enumerate(expenses, 1):
        label = _build_label(exp.description, exp.category, known_categories)
        amount = exp.amount if exp.amount is not None else "?"
        currency = exp.currency

        # Payer display.
        if exp.payer == "user":
            payer_str = "you paid"
        elif exp.payer == "partner":
            payer_str = "partner paid"
        else:
            payer_str = "payer unknown"

        # Split display.
        p_pct = exp.split_payer_pct
        o_pct = exp.split_other_pct
        if p_pct is not None and o_pct is not None:
            split_str = f"split {p_pct:g}/{o_pct:g}"

            # Calculate what the other person owes.
            if exp.amount is not None:
                owed = exp.amount * (o_pct / 100)
                if exp.payer == "user":
                    owe_str = f" \u2192 partner owes {currency} {owed:g}"
                elif exp.payer == "partner":
                    owe_str = f" \u2192 you owe {currency} {owed:g}"
                else:
                    owe_str = ""
            else:
                owe_str = ""
        else:
            split_str = "split unknown"
            owe_str = ""

        # Date display.
        date_str = exp.event_date or "today"

        lines.append(
            f"{i}. <b>{label}</b> — {currency} {amount}\n"
            f"   {payer_str}, {split_str}{owe_str}\n"
            f"   Date: {date_str}"
        )
        if exp.notes:
            lines.append(f"   Notes: {'; '.join(exp.notes)}")

    return "\n".join(lines)


def format_balance(
    balance: Decimal,
    currency: str = "ILS",
    creditor_name: str = "Partner",
) -> str:
    """Format a balance amount into a human-readable string.

    Args:
        balance: Signed balance amount. Positive means the creditor is owed;
            negative means the creditor owes.
        currency: Currency code.
        creditor_name: Display name of the creditor partner.

    Returns:
        An HTML-formatted balance string.
    """
    if balance == 0:
        return "You're all settled up! No outstanding balance."

    abs_amount = abs(balance)
    if balance > 0:
        return f"{creditor_name} owes you <b>{currency} {abs_amount}</b>."
    return f"You owe {creditor_name} <b>{currency} {abs_amount}</b>."


# ── Query result formatters (Phase 5) ─────────────────────────────────────────


def format_query_result(result: dict[str, Any], tool_name: str) -> str:
    """Format a query tool result into a Telegram HTML message.

    Dispatches to a specific formatter based on the tool that produced
    the result.

    Args:
        result: The dict returned by a query tool.
        tool_name: Name of the tool that produced the result.

    Returns:
        An HTML-formatted string suitable for ``parse_mode=HTML``.
    """
    if tool_name == "get_balance":
        return _format_balance_result(result)
    elif tool_name == "query_expenses":
        return _format_expense_query_result(result)
    elif tool_name == "get_recent_entries":
        return format_recent_entries(result.get("entries", []))
    return result.get("description", "<i>No results.</i>")


def _format_balance_result(result: dict[str, Any]) -> str:
    """Format a get_balance tool result."""
    description = result.get("description", "")
    balance_str = result.get("balance", "0")
    currency = result.get("currency", "ILS")
    who_owes = result.get("who_owes", "settled")

    if who_owes == "settled":
        return "\u2705 You're all settled up! No outstanding balance."

    try:
        amount = abs(Decimal(balance_str))
    except Exception:
        amount = balance_str

    if who_owes == "partner_owes_user":
        return f"\U0001f4b0 Partner owes you <b>{currency} {amount}</b>."
    elif who_owes == "user_owes_partner":
        return f"\U0001f4b0 You owe your partner <b>{currency} {amount}</b>."

    return description or "<i>Balance information unavailable.</i>"


def _format_expense_query_result(result: dict[str, Any]) -> str:
    """Format a query_expenses tool result."""
    count = result.get("count", 0)
    total = result.get("total", "0")
    currency = result.get("currency", "ILS")
    entries = result.get("entries", [])
    categories = result.get("categories", [])

    if count == 0:
        return "<i>No matching expenses found.</i>"

    if categories:
        header = f"\U0001f50d <b>Totals by category</b> (total <b>{currency} {total}</b>):\n"
        lines: list[str] = [header]
        for i, item in enumerate(categories, 1):
            cat = item.get("category", "uncategorized")
            cat_total = item.get("total", "0")
            cat_count = item.get("count", 0)
            lines.append(f"{i}. <b>{cat}</b> — {currency} {cat_total} ({cat_count} entries)")
        return "\n".join(lines)

    header = (
        f"\U0001f50d <b>{count} expense{'s' if count != 1 else ''}</b> "
        f"totalling <b>{currency} {total}</b>:\n"
    )
    lines: list[str] = [header]

    # Show up to 10 entries in the message.
    for i, entry in enumerate(entries[:10], 1):
        entry_date = entry.get("date", "")
        entry_amount = entry.get("amount", "?")
        entry_currency = entry.get("currency", currency)
        label = entry.get("description", entry.get("category", "expense"))
        payer = entry.get("payer", "")
        lines.append(
            f"{i}. <b>{label}</b> — {entry_currency} {entry_amount} ({payer}, {entry_date})"
        )

    if count > 10:
        lines.append(f"\n<i>... and {count - 10} more.</i>")

    return "\n".join(lines)


def format_recent_entries(entries: list[dict[str, Any]]) -> str:
    """Format a list of recent ledger entries for display.

    Args:
        entries: List of entry summary dicts (from query tools).

    Returns:
        An HTML-formatted string suitable for ``parse_mode=HTML``.
    """
    if not entries:
        return "<i>No recent entries.</i>"

    lines: list[str] = [f"\U0001f4cb <b>Recent entries ({len(entries)}):</b>\n"]

    for i, entry in enumerate(entries, 1):
        entry_date = entry.get("date", "")
        entry_type = entry.get("type", "expense")
        entry_amount = entry.get("amount", "?")
        entry_currency = entry.get("currency", "ILS")
        label = entry.get("description", entry.get("category", entry_type))
        payer = entry.get("payer", "")

        type_icon = "\U0001f4b8" if entry_type == "settlement" else "\U0001f6d2"
        lines.append(
            f"{type_icon} {i}. <b>{label}</b> — {entry_currency} {entry_amount} "
            f"({payer}, {entry_date})"
        )

    return "\n".join(lines)


def format_settlement_confirmation(expense: PendingExpense) -> str:
    """Format a settlement for the confirmation step.

    Args:
        expense: A :class:`PendingExpense` representing the settlement.

    Returns:
        An HTML-formatted confirmation message.
    """
    amount = expense.amount if expense.amount is not None else "?"
    currency = expense.currency

    if expense.payer == "user":
        payer_str = "You"
        direction = "to your partner"
    elif expense.payer == "partner":
        payer_str = "Partner"
        direction = "to you"
    else:
        payer_str = "?"
        direction = ""

    date_str = expense.event_date or "today"
    desc = expense.description or "Settlement payment"

    lines = [
        "\U0001f4b8 <b>Settlement:</b>\n",
        f"<b>{payer_str}</b> pays <b>{currency} {amount}</b> {direction}",
        f"Description: {desc}",
        f"Date: {date_str}",
    ]
    if expense.notes:
        lines.append(f"Notes: {'; '.join(expense.notes)}")
    return "\n".join(lines) + "\n"
