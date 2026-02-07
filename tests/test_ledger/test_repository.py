"""Tests for the ledger repository (raw_input persistence + ledger writes)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from finbot.ledger.repository import save_ledger_entry, save_raw_input


@pytest.mark.asyncio
async def test_save_raw_input_creates_row() -> None:
    """save_raw_input should add a RawInput to the session and flush."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    result = await save_raw_input(
        session=session,
        telegram_user_id=42,
        raw_text="groceries 300, I paid",
    )

    # The function should have called session.add with a RawInput instance.
    session.add.assert_called_once()
    added_obj = session.add.call_args[0][0]
    assert added_obj.telegram_user_id == 42
    assert added_obj.raw_text == "groceries 300, I paid"

    # It should flush to populate the id.
    session.flush.assert_called_once()

    # Return value is the added object.
    assert result is added_obj


@pytest.mark.asyncio
async def test_save_raw_input_preserves_exact_text() -> None:
    """Raw text should be stored exactly as received, no trimming."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    text_with_whitespace = "  groceries 300  \n  split 50/50  "

    result = await save_raw_input(
        session=session,
        telegram_user_id=99,
        raw_text=text_with_whitespace,
    )

    assert result.raw_text == text_with_whitespace


# ── save_ledger_entry tests (Phase 4) ────────────────────────────────────────


@pytest.mark.asyncio
async def test_save_ledger_entry_creates_row() -> None:
    """save_ledger_entry should add a LedgerEntry and flush."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    raw_id = uuid4()
    result = await save_ledger_entry(
        session,
        raw_input_id=raw_id,
        event_type="expense",
        amount=Decimal("300.00"),
        currency="ILS",
        category="groceries",
        payer_telegram_id=42,
        split_payer_pct=Decimal("50.00"),
        split_other_pct=Decimal("50.00"),
        event_date=date(2025, 12, 5),
        description="weekly groceries",
    )

    session.add.assert_called_once()
    added = session.add.call_args[0][0]
    assert added.raw_input_id == raw_id
    assert added.event_type == "expense"
    assert added.amount == Decimal("300.00")
    assert added.currency == "ILS"
    assert added.category == "groceries"
    assert added.payer_telegram_id == 42
    assert added.split_payer_pct == Decimal("50.00")
    assert added.split_other_pct == Decimal("50.00")
    assert added.event_date == date(2025, 12, 5)
    assert added.description == "weekly groceries"

    session.flush.assert_called_once()
    assert result is added


@pytest.mark.asyncio
async def test_save_ledger_entry_defaults() -> None:
    """Default currency should be ILS and optional fields should be None."""
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    result = await save_ledger_entry(
        session,
        raw_input_id=uuid4(),
        event_type="settlement",
        amount=Decimal("500.00"),
        payer_telegram_id=99,
        split_payer_pct=Decimal("100.00"),
        split_other_pct=Decimal("0.00"),
        event_date=date(2025, 12, 6),
    )

    assert result.currency == "ILS"
    assert result.description is None
    assert result.tags is None
    assert result.category is None
