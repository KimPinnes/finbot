"""Tests for Telegram bot handlers.

Uses unittest.mock to simulate aiogram Message / CallbackQuery objects
and DB sessions, verifying that handlers produce the expected replies,
persist raw input, and wire through the orchestrator correctly.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from finbot.agent.orchestrator import (
    OrchestratorResult,
    _looks_like_settlement,
    _postprocess_parsed_expenses,
)
from finbot.bot.handlers import (
    cmd_balance,
    cmd_help,
    cmd_start,
    handle_callback,
    handle_text,
)
from finbot.bot.formatters import format_query_result

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_message(text: str = "", user_id: int = 111) -> MagicMock:
    """Create a minimal mock of an aiogram ``Message``."""
    msg = AsyncMock()
    msg.text = text
    msg.from_user = MagicMock()
    msg.from_user.id = user_id
    msg.answer = AsyncMock()
    # answer() returns a sent message with a message_id.
    sent = MagicMock()
    sent.message_id = 999
    msg.answer.return_value = sent
    return msg


def _make_callback_query(
    data: str = "confirm:", user_id: int = 111,
) -> MagicMock:
    """Create a minimal mock of an aiogram ``CallbackQuery``."""
    cq = AsyncMock()
    cq.data = data
    cq.from_user = MagicMock()
    cq.from_user.id = user_id
    cq.answer = AsyncMock()
    cq.message = AsyncMock()
    cq.message.answer = AsyncMock()
    cq.message.edit_text = AsyncMock()
    cq.message.edit_reply_markup = AsyncMock()
    # message.answer() returns a sent message.
    sent = MagicMock()
    sent.message_id = 1000
    cq.message.answer.return_value = sent
    return cq


def _make_orchestrator_result(
    reply_text: str = "OK",
    keyboard: MagicMock | None = None,
    edit_message_id: int | None = None,
) -> OrchestratorResult:
    """Build an OrchestratorResult for testing."""
    return OrchestratorResult(
        reply_text=reply_text,
        keyboard=keyboard,
        edit_message_id=edit_message_id,
        llm_responses=[],
    )


# ── Command tests ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cmd_start_sends_welcome() -> None:
    msg = _make_message()
    await cmd_start(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "Welcome to FinBot" in text


@pytest.mark.asyncio
async def test_cmd_help_sends_help() -> None:
    msg = _make_message()
    await cmd_help(msg)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "How to use FinBot" in text


@pytest.mark.asyncio
async def test_cmd_balance_no_partnership() -> None:
    """Balance command without a partnership should inform the user."""
    msg = _make_message()
    session = AsyncMock()

    with patch(
        "finbot.ledger.repository.get_partnership",
        new_callable=AsyncMock,
        return_value=None,
    ):
        await cmd_balance(msg, session)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "partnership" in text.lower() or "partner" in text.lower()


@pytest.mark.asyncio
async def test_cmd_balance_with_partnership() -> None:
    """Balance command with a partnership should show the balance."""
    msg = _make_message()
    session = AsyncMock()

    mock_partnership = MagicMock()
    mock_partnership.user_a_telegram_id = 42
    mock_partnership.user_b_telegram_id = 99
    mock_partnership.default_currency = "ILS"

    with (
        patch(
            "finbot.ledger.repository.get_partnership",
            new_callable=AsyncMock,
            return_value=mock_partnership,
        ),
        patch(
            "finbot.ledger.repository.get_partner_id",
            return_value=99,
        ),
        patch(
            "finbot.ledger.balance.get_balance",
            new_callable=AsyncMock,
            return_value=Decimal("150"),
        ),
    ):
        await cmd_balance(msg, session)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "150" in text


# ── General text handler tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_text_saves_raw_input_and_processes() -> None:
    """The text handler should persist the message and send it through the orchestrator."""
    msg = _make_message(text="groceries 300", user_id=42)

    fake_raw = MagicMock()
    fake_raw.id = uuid4()

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    orch_result = _make_orchestrator_result(reply_text="Parsed 1 expense(s).")

    with (
        patch(
            "finbot.bot.handlers.save_raw_input",
            new_callable=AsyncMock,
            return_value=fake_raw,
        ) as mock_save,
        patch(
            "finbot.bot.handlers.process_message",
            new_callable=AsyncMock,
            return_value=orch_result,
        ) as mock_process,
    ):
        await handle_text(msg, session=session)

        mock_save.assert_called_once_with(
            session=session,
            telegram_user_id=42,
            raw_text="groceries 300",
        )
        mock_process.assert_called_once_with(
            user_id=42,
            text="groceries 300",
            session=session,
            raw_input_id=fake_raw.id,
        )

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "Parsed" in text


@pytest.mark.asyncio
async def test_handle_text_with_keyboard() -> None:
    """When the orchestrator returns a keyboard, it should be attached to the reply."""
    msg = _make_message(text="groceries 300 I paid 50/50", user_id=42)

    fake_raw = MagicMock()
    fake_raw.id = uuid4()

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    fake_keyboard = MagicMock()
    orch_result = _make_orchestrator_result(
        reply_text="Confirm?", keyboard=fake_keyboard,
    )

    with (
        patch(
            "finbot.bot.handlers.save_raw_input",
            new_callable=AsyncMock,
            return_value=fake_raw,
        ),
        patch(
            "finbot.bot.handlers.process_message",
            new_callable=AsyncMock,
            return_value=orch_result,
        ),
    ):
        await handle_text(msg, session=session)

    msg.answer.assert_called_once()
    call_kwargs = msg.answer.call_args
    assert call_kwargs.kwargs.get("reply_markup") is fake_keyboard


@pytest.mark.asyncio
async def test_handle_text_handles_llm_failure() -> None:
    """When the orchestrator returns an error, the handler should still reply."""
    msg = _make_message(text="groceries 300", user_id=42)

    fake_raw = MagicMock()
    fake_raw.id = uuid4()

    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    orch_result = _make_orchestrator_result(
        reply_text="I'm having trouble processing your message.",
    )

    with (
        patch(
            "finbot.bot.handlers.save_raw_input",
            new_callable=AsyncMock,
            return_value=fake_raw,
        ),
        patch(
            "finbot.bot.handlers.process_message",
            new_callable=AsyncMock,
            return_value=orch_result,
        ),
    ):
        await handle_text(msg, session=session)

    msg.answer.assert_called_once()
    text = msg.answer.call_args[0][0]
    assert "trouble" in text.lower()


@pytest.mark.asyncio
async def test_handle_text_ignores_empty_user() -> None:
    """Messages without from_user should be silently ignored."""
    msg = AsyncMock()
    msg.from_user = None
    msg.text = "hello"
    msg.answer = AsyncMock()

    session = AsyncMock()
    await handle_text(msg, session=session)

    msg.answer.assert_not_called()


@pytest.mark.asyncio
async def test_handle_text_ignores_empty_text() -> None:
    """Messages without text should be silently ignored."""
    msg = _make_message(text="", user_id=42)
    msg.text = None

    session = AsyncMock()
    await handle_text(msg, session=session)

    msg.answer.assert_not_called()


# ── Callback query handler tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_handle_callback_confirm() -> None:
    """Confirm callback should call process_callback and send the reply."""
    cq = _make_callback_query(data="confirm:abc", user_id=42)
    session = AsyncMock()

    orch_result = _make_orchestrator_result(
        reply_text="Committed 1 expense.",
    )

    with patch(
        "finbot.bot.handlers.process_callback",
        new_callable=AsyncMock,
        return_value=orch_result,
    ) as mock_cb:
        await handle_callback(cq, session=session)

        mock_cb.assert_called_once_with(
            user_id=42,
            callback_data="confirm:abc",
            session=session,
        )

    cq.answer.assert_called_once()
    cq.message.answer.assert_called_once()
    assert "Committed" in cq.message.answer.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_callback_cancel() -> None:
    """Cancel callback should send cancellation message."""
    cq = _make_callback_query(data="cancel:", user_id=42)
    session = AsyncMock()

    orch_result = _make_orchestrator_result(
        reply_text="Cancelled. No expenses were recorded.",
    )

    with patch(
        "finbot.bot.handlers.process_callback",
        new_callable=AsyncMock,
        return_value=orch_result,
    ):
        await handle_callback(cq, session=session)

    cq.answer.assert_called_once()
    cq.message.answer.assert_called_once()
    assert "cancel" in cq.message.answer.call_args[0][0].lower()


@pytest.mark.asyncio
async def test_handle_callback_edit_message_id() -> None:
    """When edit_message_id is set, should try to edit the original message."""
    cq = _make_callback_query(data="confirm:", user_id=42)
    session = AsyncMock()

    orch_result = _make_orchestrator_result(
        reply_text="Updated.", edit_message_id=555,
    )

    with patch(
        "finbot.bot.handlers.process_callback",
        new_callable=AsyncMock,
        return_value=orch_result,
    ):
        await handle_callback(cq, session=session)

    cq.answer.assert_called_once()
    cq.message.edit_text.assert_called_once()
    assert "Updated" in cq.message.edit_text.call_args[0][0]


@pytest.mark.asyncio
async def test_handle_callback_ignores_empty_data() -> None:
    """Callbacks without data should be acknowledged and ignored."""
    cq = AsyncMock()
    cq.from_user = MagicMock()
    cq.from_user.id = 42
    cq.data = None
    cq.answer = AsyncMock()

    session = AsyncMock()
    await handle_callback(cq, session=session)

    cq.answer.assert_called_once()


@pytest.mark.asyncio
async def test_handle_callback_ignores_empty_user() -> None:
    """Callbacks without from_user should be acknowledged and ignored."""
    cq = AsyncMock()
    cq.from_user = None
    cq.data = "confirm:"
    cq.answer = AsyncMock()

    session = AsyncMock()
    await handle_callback(cq, session=session)

    cq.answer.assert_called_once()


def test_postprocess_relative_date_yesterday() -> None:
    parsed = {
        "expenses": [{"amount": 240, "event_date": "2023-10-07"}],
    }
    with patch("finbot.agent.orchestrator.date") as mock_date:
        mock_date.today.return_value = date(2026, 2, 7)
        result = _postprocess_parsed_expenses("gas 240 yesterday", parsed)

    assert result["expenses"][0]["event_date"] == "2026-02-06"


def test_postprocess_relative_date_one_week() -> None:
    parsed = {
        "expenses": [{"amount": 300, "event_date": "2023-10-21"}],
    }
    with patch("finbot.agent.orchestrator.date") as mock_date:
        mock_date.today.return_value = date(2026, 2, 7)
        result = _postprocess_parsed_expenses("water bill one week ago 300", parsed)

    assert result["expenses"][0]["event_date"] == "2026-01-31"


def test_postprocess_amount_truncation() -> None:
    parsed = {"expenses": [{"amount": 300}]}
    result = _postprocess_parsed_expenses("partner paid 2000 for nails", parsed)

    assert result["expenses"][0]["amount"] == 2000


def test_looks_like_settlement_phrase() -> None:
    assert _looks_like_settlement("partner settled 300") is True


def test_format_query_result_category_totals() -> None:
    result = {
        "count": 3,
        "total": "600",
        "currency": "ILS",
        "categories": [
            {"category": "gas", "total": "300", "count": 1},
            {"category": "groceries", "total": "300", "count": 2},
        ],
        "entries": [],
    }

    text = format_query_result(result, "query_expenses")
    assert "Totals by category" in text
    assert "groceries" in text
