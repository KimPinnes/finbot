"""Tests for balance derivation from ledger replay."""

from __future__ import annotations

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from finbot.ledger.balance import (
    _entry_effect,
    _expense_effect,
    _settlement_effect,
    get_balance,
)


# ── Helpers ───────────────────────────────────────────────────────────────────

USER_A = 100
USER_B = 200


def _make_entry(
    *,
    event_type: str = "expense",
    amount: Decimal = Decimal("300"),
    payer_telegram_id: int = USER_A,
    split_payer_pct: Decimal = Decimal("50"),
    split_other_pct: Decimal = Decimal("50"),
) -> MagicMock:
    """Create a mock LedgerEntry with sensible defaults for testing."""
    entry = MagicMock()
    entry.event_type = event_type
    entry.amount = amount
    entry.payer_telegram_id = payer_telegram_id
    entry.split_payer_pct = split_payer_pct
    entry.split_other_pct = split_other_pct
    return entry


# ── _expense_effect tests ─────────────────────────────────────────────────────


class TestExpenseEffect:
    """Tests for the _expense_effect helper."""

    def test_user_a_pays_50_50(self) -> None:
        """User A pays 300, split 50/50 → B owes A 150."""
        entry = _make_entry(
            payer_telegram_id=USER_A,
            amount=Decimal("300"),
            split_payer_pct=Decimal("50"),
            split_other_pct=Decimal("50"),
        )
        result = _expense_effect(entry, USER_A, USER_B)
        assert result == Decimal("150")

    def test_user_b_pays_50_50(self) -> None:
        """User B pays 300, split 50/50 → A owes B 150 (negative)."""
        entry = _make_entry(
            payer_telegram_id=USER_B,
            amount=Decimal("300"),
            split_payer_pct=Decimal("50"),
            split_other_pct=Decimal("50"),
        )
        result = _expense_effect(entry, USER_A, USER_B)
        assert result == Decimal("-150")

    def test_user_a_pays_70_30(self) -> None:
        """User A pays 300, split 70/30 → B owes A 90."""
        entry = _make_entry(
            payer_telegram_id=USER_A,
            amount=Decimal("300"),
            split_payer_pct=Decimal("70"),
            split_other_pct=Decimal("30"),
        )
        result = _expense_effect(entry, USER_A, USER_B)
        assert result == Decimal("90")

    def test_user_a_pays_100_0(self) -> None:
        """User A pays 200, split 100/0 → B owes nothing."""
        entry = _make_entry(
            payer_telegram_id=USER_A,
            amount=Decimal("200"),
            split_payer_pct=Decimal("100"),
            split_other_pct=Decimal("0"),
        )
        result = _expense_effect(entry, USER_A, USER_B)
        assert result == Decimal("0")

    def test_unknown_payer_no_effect(self) -> None:
        """An entry with an unknown payer has no effect."""
        entry = _make_entry(payer_telegram_id=999)
        result = _expense_effect(entry, USER_A, USER_B)
        assert result == Decimal("0")


# ── _settlement_effect tests ──────────────────────────────────────────────────


class TestSettlementEffect:
    """Tests for the _settlement_effect helper."""

    def test_user_a_pays_settlement(self) -> None:
        """User A settles 500 → balance moves positive (B owes A more)."""
        entry = _make_entry(
            event_type="settlement",
            payer_telegram_id=USER_A,
            amount=Decimal("500"),
        )
        result = _settlement_effect(entry, USER_A, USER_B)
        assert result == Decimal("500")

    def test_user_b_pays_settlement(self) -> None:
        """User B settles 500 → balance moves negative (A owes B more)."""
        entry = _make_entry(
            event_type="settlement",
            payer_telegram_id=USER_B,
            amount=Decimal("500"),
        )
        result = _settlement_effect(entry, USER_A, USER_B)
        assert result == Decimal("-500")

    def test_unknown_payer_no_effect(self) -> None:
        """Settlement with unknown payer has no effect."""
        entry = _make_entry(event_type="settlement", payer_telegram_id=999)
        result = _settlement_effect(entry, USER_A, USER_B)
        assert result == Decimal("0")


# ── _entry_effect tests ───────────────────────────────────────────────────────


class TestEntryEffect:
    """Tests for the _entry_effect dispatcher."""

    def test_expense_dispatches(self) -> None:
        entry = _make_entry(event_type="expense")
        result = _entry_effect(entry, USER_A, USER_B)
        # Should return the expense effect (150 for default 300 @ 50/50).
        assert result == Decimal("150")

    def test_correction_dispatches_like_expense(self) -> None:
        entry = _make_entry(event_type="correction")
        result = _entry_effect(entry, USER_A, USER_B)
        assert result == Decimal("150")

    def test_settlement_dispatches(self) -> None:
        entry = _make_entry(event_type="settlement", payer_telegram_id=USER_A)
        result = _entry_effect(entry, USER_A, USER_B)
        assert result == Decimal("300")  # Full amount as settlement.

    def test_unknown_event_type_ignored(self) -> None:
        entry = _make_entry(event_type="unknown_type")
        result = _entry_effect(entry, USER_A, USER_B)
        assert result == Decimal("0")


# ── get_balance integration tests ─────────────────────────────────────────────


class TestGetBalance:
    """Tests for the get_balance function with pre-fetched entries."""

    @pytest.mark.asyncio
    async def test_no_entries_returns_zero(self) -> None:
        """Empty ledger → zero balance."""
        session = AsyncMock()
        result = await get_balance(session, USER_A, USER_B, entries=[])
        assert result == Decimal("0")

    @pytest.mark.asyncio
    async def test_single_expense(self) -> None:
        """User A pays 300, split 50/50 → balance = 150 (B owes A)."""
        entry = _make_entry(
            payer_telegram_id=USER_A,
            amount=Decimal("300"),
            split_payer_pct=Decimal("50"),
            split_other_pct=Decimal("50"),
        )
        session = AsyncMock()
        result = await get_balance(session, USER_A, USER_B, entries=[entry])
        assert result == Decimal("150")

    @pytest.mark.asyncio
    async def test_multiple_expenses_different_payers(self) -> None:
        """A pays 300 (50/50) + B pays 200 (50/50) → net = 150 - 100 = 50."""
        e1 = _make_entry(
            payer_telegram_id=USER_A,
            amount=Decimal("300"),
            split_payer_pct=Decimal("50"),
            split_other_pct=Decimal("50"),
        )
        e2 = _make_entry(
            payer_telegram_id=USER_B,
            amount=Decimal("200"),
            split_payer_pct=Decimal("50"),
            split_other_pct=Decimal("50"),
        )
        session = AsyncMock()
        result = await get_balance(session, USER_A, USER_B, entries=[e1, e2])
        assert result == Decimal("50")  # B still owes A 50.

    @pytest.mark.asyncio
    async def test_settlement_reduces_balance(self) -> None:
        """A pays 300 (50/50), then B settles 100 → balance = 150 - 100 = 50."""
        expense = _make_entry(
            payer_telegram_id=USER_A,
            amount=Decimal("300"),
            split_payer_pct=Decimal("50"),
            split_other_pct=Decimal("50"),
        )
        settlement = _make_entry(
            event_type="settlement",
            payer_telegram_id=USER_B,
            amount=Decimal("100"),
        )
        session = AsyncMock()
        result = await get_balance(
            session, USER_A, USER_B, entries=[expense, settlement],
        )
        assert result == Decimal("50")

    @pytest.mark.asyncio
    async def test_settlement_clears_balance(self) -> None:
        """A pays 300 (50/50), then B settles 150 → balance = 0."""
        expense = _make_entry(
            payer_telegram_id=USER_A,
            amount=Decimal("300"),
            split_payer_pct=Decimal("50"),
            split_other_pct=Decimal("50"),
        )
        settlement = _make_entry(
            event_type="settlement",
            payer_telegram_id=USER_B,
            amount=Decimal("150"),
        )
        session = AsyncMock()
        result = await get_balance(
            session, USER_A, USER_B, entries=[expense, settlement],
        )
        assert result == Decimal("0")

    @pytest.mark.asyncio
    async def test_complex_scenario(self) -> None:
        """Multiple expenses and a partial settlement."""
        entries = [
            # A pays 400, split 60/40 → B owes 160
            _make_entry(
                payer_telegram_id=USER_A,
                amount=Decimal("400"),
                split_payer_pct=Decimal("60"),
                split_other_pct=Decimal("40"),
            ),
            # B pays 200, split 50/50 → A owes 100 → net +60
            _make_entry(
                payer_telegram_id=USER_B,
                amount=Decimal("200"),
                split_payer_pct=Decimal("50"),
                split_other_pct=Decimal("50"),
            ),
            # B settles 30 → net +60 - 30 = +30
            _make_entry(
                event_type="settlement",
                payer_telegram_id=USER_B,
                amount=Decimal("30"),
            ),
        ]
        session = AsyncMock()
        result = await get_balance(session, USER_A, USER_B, entries=entries)
        assert result == Decimal("30")
