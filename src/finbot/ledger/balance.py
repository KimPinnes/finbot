"""Balance derivation from ledger replay.

Provides :func:`get_balance` which computes the net balance between two
partners by replaying all active (non-superseded) ledger entries.

The balance is **always derived**, never stored — see design.md §6.3.
"""

from __future__ import annotations

from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.ledger.models import LedgerEntry


async def get_balance(
    session: AsyncSession,
    user_a_id: int,
    user_b_id: int,
    entries: list[LedgerEntry] | None = None,
) -> Decimal:
    """Derive the net balance between two partners.

    Replays every active ledger entry involving either partner and
    accumulates how much ``user_b`` owes ``user_a``.

    Args:
        session: Async database session (used only if *entries* is ``None``).
        user_a_id: Telegram user ID of the first partner.
        user_b_id: Telegram user ID of the second partner.
        entries: Pre-fetched active entries (optional — if ``None``, they are
            loaded from the DB via :func:`get_active_ledger_entries`).

    Returns:
        A signed :class:`~decimal.Decimal`:

        - **positive** → ``user_b`` owes ``user_a``
        - **negative** → ``user_a`` owes ``user_b``
        - **zero** → settled up
    """
    if entries is None:
        from finbot.ledger.repository import get_active_ledger_entries

        entries = await get_active_ledger_entries(session, user_a_id, user_b_id)

    # Accumulate: positive = user_b owes user_a.
    balance = Decimal("0")

    for entry in entries:
        balance += _entry_effect(entry, user_a_id, user_b_id)

    return balance


def _entry_effect(
    entry: LedgerEntry,
    user_a_id: int,
    user_b_id: int,
) -> Decimal:
    """Compute how a single ledger entry affects the balance.

    Returns a signed amount: positive means ``user_b`` owes more to ``user_a``,
    negative means ``user_a`` owes more to ``user_b``.
    """
    if entry.event_type in ("expense", "correction"):
        return _expense_effect(entry, user_a_id, user_b_id)
    elif entry.event_type == "settlement":
        return _settlement_effect(entry, user_a_id, user_b_id)
    # Unknown event types are ignored.
    return Decimal("0")


def _expense_effect(
    entry: LedgerEntry,
    user_a_id: int,
    user_b_id: int,
) -> Decimal:
    """Compute the balance effect of an expense or correction.

    The payer paid the full ``amount``.  The other partner's share is
    ``amount * (split_other_pct / 100)`` — that is the amount the other
    partner owes the payer.

    Returns:
        Positive if ``user_b`` owes ``user_a``, negative otherwise.
    """
    amount = entry.amount
    other_share = amount * entry.split_other_pct / Decimal("100")

    if entry.payer_telegram_id == user_a_id:
        # user_a paid → user_b owes user_a the other's share → positive.
        return other_share
    elif entry.payer_telegram_id == user_b_id:
        # user_b paid → user_a owes user_b the other's share → negative.
        return -other_share
    # Payer is neither partner (shouldn't happen) — no effect.
    return Decimal("0")


def _settlement_effect(
    entry: LedgerEntry,
    user_a_id: int,
    user_b_id: int,
) -> Decimal:
    """Compute the balance effect of a settlement.

    A settlement is a direct payment from the payer to the other partner.
    It *reduces* whatever the payer owes (or increases what they are owed).

    If ``user_a`` pays ``user_b`` a settlement of 500, it means ``user_a``
    is reducing their debt to ``user_b`` (or overpaying — the balance just
    shifts).  Effect: balance moves *positive* (user_b owes user_a more /
    user_a owes user_b less).

    Returns:
        Positive if the settlement shifts the balance toward user_b owing
        user_a, negative otherwise.
    """
    amount = entry.amount

    if entry.payer_telegram_id == user_a_id:
        # user_a paid user_b → reduces user_a's debt → balance goes positive.
        return amount
    elif entry.payer_telegram_id == user_b_id:
        # user_b paid user_a → reduces user_b's debt → balance goes negative.
        return -amount
    return Decimal("0")
