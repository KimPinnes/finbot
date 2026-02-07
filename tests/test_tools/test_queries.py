"""Tests for query tools (get_balance, query_expenses, get_recent_entries)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from finbot.tools.queries import get_balance, get_recent_entries, query_expenses

# ── Helpers ───────────────────────────────────────────────────────────────────

USER_ID = 100
PARTNER_ID = 200


def _mock_partnership():
    """Create a mock Partnership object."""
    p = MagicMock()
    p.user_a_telegram_id = USER_ID
    p.user_b_telegram_id = PARTNER_ID
    p.default_currency = "ILS"
    return p


def _mock_entry(
    *,
    event_type: str = "expense",
    amount: Decimal = Decimal("300"),
    payer_id: int = USER_ID,
    category: str = "groceries",
    event_date: date = date(2025, 12, 5),
    description: str | None = None,
    currency: str = "ILS",
) -> MagicMock:
    """Create a mock LedgerEntry."""
    entry = MagicMock()
    entry.id = uuid4()
    entry.event_type = event_type
    entry.amount = amount
    entry.currency = currency
    entry.category = category
    entry.payer_telegram_id = payer_id
    entry.event_date = event_date
    entry.description = description
    return entry


# ── get_balance tests ─────────────────────────────────────────────────────────


class TestGetBalance:
    """Tests for the get_balance query tool."""

    @pytest.mark.asyncio
    async def test_missing_session_returns_error(self) -> None:
        result = await get_balance()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_partnership_returns_error(self) -> None:
        session = AsyncMock()
        with patch(
            "finbot.tools.queries.get_partnership",
            return_value=None,
        ):
            result = await get_balance(session=session, user_id=USER_ID)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_partner_owes_user(self) -> None:
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries._derive_balance",
                return_value=Decimal("150"),
            ),
        ):
            result = await get_balance(session=session, user_id=USER_ID)

        assert result["who_owes"] == "partner_owes_user"
        assert result["balance"] == "150"
        assert result["currency"] == "ILS"

    @pytest.mark.asyncio
    async def test_user_owes_partner(self) -> None:
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries._derive_balance",
                return_value=Decimal("-200"),
            ),
        ):
            result = await get_balance(session=session, user_id=USER_ID)

        assert result["who_owes"] == "user_owes_partner"
        assert result["balance"] == "-200"

    @pytest.mark.asyncio
    async def test_settled_up(self) -> None:
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries._derive_balance",
                return_value=Decimal("0"),
            ),
        ):
            result = await get_balance(session=session, user_id=USER_ID)

        assert result["who_owes"] == "settled"


# ── query_expenses tests ──────────────────────────────────────────────────────


class TestQueryExpenses:
    """Tests for the query_expenses tool."""

    @pytest.mark.asyncio
    async def test_missing_session_returns_error(self) -> None:
        result = await query_expenses()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_matching_entries(self) -> None:
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries.get_filtered_entries",
                return_value=[],
            ),
        ):
            result = await query_expenses(
                category="unicorns",
                session=session,
                user_id=USER_ID,
            )

        assert result["count"] == 0
        assert result["total"] == "0"
        assert result["entries"] == []

    @pytest.mark.asyncio
    async def test_matching_entries(self) -> None:
        session = AsyncMock()
        entries = [
            _mock_entry(amount=Decimal("300"), category="groceries"),
            _mock_entry(amount=Decimal("200"), category="groceries"),
        ]

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries.get_filtered_entries",
                return_value=entries,
            ),
        ):
            result = await query_expenses(
                category="groceries",
                session=session,
                user_id=USER_ID,
            )

        assert result["count"] == 2
        assert result["total"] == "500"
        assert result["currency"] == "ILS"
        assert len(result["entries"]) == 2

    @pytest.mark.asyncio
    async def test_date_filters_passed_through(self) -> None:
        """date_from and date_to should be parsed and passed to repository."""
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries.get_filtered_entries",
                return_value=[],
            ) as mock_filter,
        ):
            await query_expenses(
                date_from="2025-12-01",
                date_to="2025-12-31",
                session=session,
                user_id=USER_ID,
            )

        call_kwargs = mock_filter.call_args[1]
        assert call_kwargs["date_from"] == date(2025, 12, 1)
        assert call_kwargs["date_to"] == date(2025, 12, 31)

    @pytest.mark.asyncio
    async def test_payer_labels(self) -> None:
        """Entries should have 'you' or 'partner' payer labels."""
        session = AsyncMock()
        entries = [
            _mock_entry(payer_id=USER_ID),
            _mock_entry(payer_id=PARTNER_ID),
        ]

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries.get_filtered_entries",
                return_value=entries,
            ),
        ):
            result = await query_expenses(session=session, user_id=USER_ID)

        assert result["entries"][0]["payer"] == "you"
        assert result["entries"][1]["payer"] == "partner"


# ── get_recent_entries tests ──────────────────────────────────────────────────


class TestGetRecentEntries:
    """Tests for the get_recent_entries tool."""

    @pytest.mark.asyncio
    async def test_missing_session_returns_error(self) -> None:
        result = await get_recent_entries()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_entries(self) -> None:
        session = AsyncMock()
        entries = [
            _mock_entry(amount=Decimal("100")),
            _mock_entry(amount=Decimal("200")),
        ]

        with (
            patch(
                "finbot.tools.queries.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.queries.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.queries._fetch_recent",
                return_value=entries,
            ),
        ):
            result = await get_recent_entries(
                limit=5,
                session=session,
                user_id=USER_ID,
            )

        assert result["count"] == 2
        assert len(result["entries"]) == 2

    @pytest.mark.asyncio
    async def test_missing_partnership_returns_error(self) -> None:
        session = AsyncMock()

        with patch(
            "finbot.tools.queries.get_partnership",
            return_value=None,
        ):
            result = await get_recent_entries(session=session, user_id=USER_ID)

        assert "error" in result
