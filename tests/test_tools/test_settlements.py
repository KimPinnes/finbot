"""Tests for settlement tools (log_settlement and validate_settlement)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from finbot.tools.settlements import log_settlement, validate_settlement_tool


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


# ── log_settlement tests ─────────────────────────────────────────────────────


class TestLogSettlement:
    """Tests for the log_settlement tool."""

    @pytest.mark.asyncio
    async def test_missing_session_returns_error(self) -> None:
        """log_settlement without session should return an error."""
        result = await log_settlement(
            amount=500, payer="user",
        )
        assert "error" in result

    @pytest.mark.asyncio
    async def test_missing_partnership_returns_error(self) -> None:
        """log_settlement without a partnership should return an error."""
        session = AsyncMock()

        with patch(
            "finbot.tools.settlements.get_partnership",
            return_value=None,
        ):
            result = await log_settlement(
                amount=500,
                payer="user",
                session=session,
                user_id=USER_ID,
                raw_input_id=uuid4(),
            )
        assert "error" in result
        assert "partnership" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_valid_settlement_commits(self) -> None:
        """A valid settlement should be committed to the ledger."""
        session = AsyncMock()
        raw_id = uuid4()
        entry_id = uuid4()

        mock_entry = MagicMock()
        mock_entry.id = entry_id

        with (
            patch(
                "finbot.tools.settlements.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.settlements.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.settlements._derive_balance",
                return_value=Decimal("500"),
            ),
            patch(
                "finbot.tools.settlements.save_ledger_entry",
                return_value=mock_entry,
            ) as mock_save,
        ):
            result = await log_settlement(
                amount=300,
                payer="user",
                description="Test settlement",
                session=session,
                user_id=USER_ID,
                raw_input_id=raw_id,
            )

        assert result["success"] is True
        assert result["entry_id"] == str(entry_id)

        # Verify save_ledger_entry was called with correct args.
        mock_save.assert_called_once()
        call_kwargs = mock_save.call_args[1]
        assert call_kwargs["event_type"] == "settlement"
        assert call_kwargs["amount"] == Decimal("300")
        assert call_kwargs["payer_telegram_id"] == USER_ID
        assert call_kwargs["split_payer_pct"] == Decimal("100")
        assert call_kwargs["split_other_pct"] == Decimal("0")

    @pytest.mark.asyncio
    async def test_negative_amount_returns_error(self) -> None:
        """Negative amount should fail validation."""
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.settlements.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.settlements.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.settlements._derive_balance",
                return_value=Decimal("0"),
            ),
        ):
            result = await log_settlement(
                amount=-100,
                payer="user",
                session=session,
                user_id=USER_ID,
                raw_input_id=uuid4(),
            )

        assert "error" in result
        assert "positive" in result["error"].lower()

    @pytest.mark.asyncio
    async def test_partner_payer(self) -> None:
        """When payer is 'partner', the partner ID should be used."""
        session = AsyncMock()
        raw_id = uuid4()
        mock_entry = MagicMock()
        mock_entry.id = uuid4()

        with (
            patch(
                "finbot.tools.settlements.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.settlements.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.settlements._derive_balance",
                return_value=Decimal("0"),
            ),
            patch(
                "finbot.tools.settlements.save_ledger_entry",
                return_value=mock_entry,
            ) as mock_save,
        ):
            result = await log_settlement(
                amount=200,
                payer="partner",
                session=session,
                user_id=USER_ID,
                raw_input_id=raw_id,
            )

        assert result["success"] is True
        call_kwargs = mock_save.call_args[1]
        assert call_kwargs["payer_telegram_id"] == PARTNER_ID


# ── validate_settlement_tool tests ───────────────────────────────────────────


class TestValidateSettlementTool:
    """Tests for the validate_settlement_tool."""

    @pytest.mark.asyncio
    async def test_missing_session_returns_error(self) -> None:
        result = await validate_settlement_tool(amount=100, payer="user")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_valid_settlement(self) -> None:
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.settlements.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.settlements.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.settlements._derive_balance",
                return_value=Decimal("300"),
            ),
        ):
            result = await validate_settlement_tool(
                amount=200,
                payer="partner",
                session=session,
                user_id=USER_ID,
            )

        assert result["valid"] is True
        assert result["errors"] == []

    @pytest.mark.asyncio
    async def test_invalid_settlement(self) -> None:
        session = AsyncMock()

        with (
            patch(
                "finbot.tools.settlements.get_partnership",
                return_value=_mock_partnership(),
            ),
            patch(
                "finbot.tools.settlements.get_partner_id",
                return_value=PARTNER_ID,
            ),
            patch(
                "finbot.tools.settlements._derive_balance",
                return_value=Decimal("0"),
            ),
        ):
            result = await validate_settlement_tool(
                amount=-50,
                payer="user",
                session=session,
                user_id=USER_ID,
            )

        assert result["valid"] is False
        assert len(result["errors"]) > 0
