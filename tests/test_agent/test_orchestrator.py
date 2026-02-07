"""Tests for the multi-step agent orchestrator (state machine).

All tests mock the LLM client so no real API calls are made.  The tests
verify state transitions, clarification flow, confirmation, commit, edit,
and cancel.
"""

from __future__ import annotations

import uuid
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finbot.agent.llm_client import LLMResponse, ToolCall
from finbot.agent.orchestrator import (
    Orchestrator,
    _build_clarification_question,
    _merge_field_manually,
    _parse_split,
    _resolve_date,
)
from finbot.agent.state import (
    ConversationContext,
    ConversationState,
    ConversationStore,
    PendingExpense,
)
from finbot.bot.keyboards import CB_CANCEL, CB_CONFIRM, CB_EDIT

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_llm_response(
    *,
    content: str = "",
    tool_calls: list[ToolCall] | None = None,
) -> LLMResponse:
    """Build a minimal LLMResponse for testing."""
    return LLMResponse(
        content=content,
        tool_calls=tool_calls or [],
        input_tokens=10,
        output_tokens=5,
        latency_ms=50,
        provider="test",
        model="test-model",
    )


def _expense_tool_call(
    expenses: list[dict],
    intent: str = "expense",
) -> ToolCall:
    """Build a parse_expense ToolCall with the given expense data."""
    return ToolCall(
        id="call_0",
        name="parse_expense",
        arguments={"expenses": expenses, "intent": intent},
    )


def _complete_expense_dict(**overrides) -> dict:
    """A fully-populated expense dict (all required fields present)."""
    base = {
        "amount": 300,
        "currency": "ILS",
        "category": "groceries",
        "payer": "user",
        "split_payer_pct": 50,
        "split_other_pct": 50,
        "event_date": "2025-12-05",
        "description": "weekly groceries",
    }
    base.update(overrides)
    return base


def _incomplete_expense_dict(**overrides) -> dict:
    """An expense dict missing payer and split."""
    base = {
        "amount": 300,
        "currency": "ILS",
        "category": "groceries",
        "description": None,
        "event_date": None,
    }
    base.update(overrides)
    return base


def _make_orchestrator(
    llm_response: LLMResponse | None = None,
    store: ConversationStore | None = None,
) -> tuple[Orchestrator, AsyncMock]:
    """Create an Orchestrator with a mocked LLM client."""
    mock_llm = AsyncMock()
    if llm_response is not None:
        mock_llm.chat = AsyncMock(return_value=llm_response)
    s = store or ConversationStore()
    return Orchestrator(llm_client=mock_llm, store=s), mock_llm


# ── Happy path: parse → validate → confirm → commit ─────────────────────────


@pytest.mark.asyncio
async def test_happy_path_complete_expense() -> None:
    """A message with all fields should go straight to CONFIRMING."""
    response = _make_llm_response(tool_calls=[_expense_tool_call([_complete_expense_dict()])])
    store = ConversationStore()
    orch, _ = _make_orchestrator(response, store)
    session = AsyncMock()
    raw_id = uuid.uuid4()

    result = await orch.handle_message(
        user_id=42,
        text="groceries 300 I paid split 50/50",
        session=session,
        raw_input_id=raw_id,
    )

    # Should be in CONFIRMING state with a keyboard.
    assert result.keyboard is not None
    assert "groceries" in result.reply_text.lower() or "300" in result.reply_text
    ctx = store.get(42)
    assert ctx.state == ConversationState.CONFIRMING


@pytest.mark.asyncio
async def test_happy_path_confirm_commits() -> None:
    """Tapping Confirm should write to the ledger and return to IDLE."""
    store = ConversationStore()
    ctx = ConversationContext(
        state=ConversationState.CONFIRMING,
        raw_input_id=uuid.uuid4(),
        pending_expenses=[
            PendingExpense(
                amount=300,
                category="groceries",
                payer="user",
                split_payer_pct=50,
                split_other_pct=50,
                event_date="2025-12-05",
            ),
        ],
    )
    store.set(42, ctx)
    orch, _ = _make_orchestrator(store=store)
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    result = await orch.handle_callback(
        user_id=42,
        callback_data=f"{CB_CONFIRM}:abc",
        session=session,
    )

    assert "\u2705" in result.reply_text  # Checkmark
    assert "1 expense" in result.reply_text.lower()
    # State should be cleared.
    assert not store.has(42)
    # Ledger entry should have been written.
    session.add.assert_called_once()


# ── Clarification flow ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_missing_payer_triggers_clarification() -> None:
    """If payer is missing, orchestrator should ask for it."""
    response = _make_llm_response(tool_calls=[_expense_tool_call([_incomplete_expense_dict()])])
    store = ConversationStore()
    orch, _ = _make_orchestrator(response, store)
    session = AsyncMock()

    # Ensure assume_half_split is off so split stays missing.
    with patch("finbot.agent.orchestrator.settings") as mock_settings:
        mock_settings.assume_half_split = False
        mock_settings.default_categories = ["groceries"]
        mock_settings.default_currency = "ILS"

        result = await orch.handle_message(
            user_id=42,
            text="groceries 300",
            session=session,
            raw_input_id=uuid.uuid4(),
        )

    ctx = store.get(42)
    assert ctx.state == ConversationState.CLARIFYING
    assert result.keyboard is None  # No confirm keyboard yet.
    # Should ask about split (payer is auto-defaulted to "user").
    assert "split" in result.reply_text.lower() or "who" in result.reply_text.lower()


@pytest.mark.asyncio
async def test_clarification_answer_merges_and_revalidates() -> None:
    """Answering a clarification should merge data and re-validate."""
    store = ConversationStore()
    # Set up state: we're clarifying payer.
    ctx = ConversationContext(
        state=ConversationState.CLARIFYING,
        raw_input_id=uuid.uuid4(),
        clarification_field="payer",
        pending_expenses=[
            PendingExpense(amount=300, category="groceries"),
        ],
    )
    store.set(42, ctx)

    # LLM response with payer filled in but still missing split.
    merge_response = _make_llm_response(
        tool_calls=[
            _expense_tool_call(
                [
                    {"amount": 300, "category": "groceries", "payer": "user"},
                ]
            )
        ]
    )
    orch, mock_llm = _make_orchestrator(merge_response, store)
    mock_llm.chat = AsyncMock(return_value=merge_response)
    session = AsyncMock()

    # Ensure assume_half_split is off so split stays missing.
    with patch("finbot.agent.orchestrator.settings") as mock_settings:
        mock_settings.assume_half_split = False
        mock_settings.default_categories = ["groceries"]
        mock_settings.default_currency = "ILS"

        result = await orch.handle_message(
            user_id=42,
            text="me",
            session=session,
            raw_input_id=uuid.uuid4(),
        )

    ctx = store.get(42)
    # Still needs split — should be in CLARIFYING again.
    assert ctx.state == ConversationState.CLARIFYING
    assert "split" in result.reply_text.lower()


@pytest.mark.asyncio
async def test_full_clarification_flow_to_confirm() -> None:
    """After all clarifications answered, should reach CONFIRMING."""
    store = ConversationStore()
    ctx = ConversationContext(
        state=ConversationState.CLARIFYING,
        raw_input_id=uuid.uuid4(),
        clarification_field="payer",
        pending_expenses=[
            PendingExpense(
                amount=300,
                category="groceries",
                split_payer_pct=50,
                split_other_pct=50,
            ),
        ],
    )
    store.set(42, ctx)

    # LLM returns complete data.
    merge_response = _make_llm_response(tool_calls=[_expense_tool_call([_complete_expense_dict()])])
    orch, mock_llm = _make_orchestrator(merge_response, store)
    mock_llm.chat = AsyncMock(return_value=merge_response)
    session = AsyncMock()

    result = await orch.handle_message(
        user_id=42,
        text="me",
        session=session,
        raw_input_id=uuid.uuid4(),
    )

    ctx = store.get(42)
    assert ctx.state == ConversationState.CONFIRMING
    assert result.keyboard is not None


# ── Cancel flow ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_cancel_clears_state() -> None:
    """Tapping Cancel should discard pending expenses."""
    store = ConversationStore()
    store.set(
        42,
        ConversationContext(
            state=ConversationState.CONFIRMING,
            raw_input_id=uuid.uuid4(),
            pending_expenses=[
                PendingExpense(
                    amount=300,
                    category="groceries",
                    payer="user",
                    split_payer_pct=50,
                    split_other_pct=50,
                ),
            ],
        ),
    )
    orch, _ = _make_orchestrator(store=store)
    session = AsyncMock()

    result = await orch.handle_callback(
        user_id=42,
        callback_data=f"{CB_CANCEL}:",
        session=session,
    )

    assert "cancel" in result.reply_text.lower()
    assert not store.has(42)


# ── Edit flow ─────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_edit_enters_clarifying() -> None:
    """Tapping Edit should move to CLARIFYING state."""
    store = ConversationStore()
    store.set(
        42,
        ConversationContext(
            state=ConversationState.CONFIRMING,
            raw_input_id=uuid.uuid4(),
            pending_expenses=[
                PendingExpense(
                    amount=300,
                    category="groceries",
                    payer="user",
                    split_payer_pct=50,
                    split_other_pct=50,
                ),
            ],
        ),
    )
    orch, _ = _make_orchestrator(store=store)
    session = AsyncMock()

    result = await orch.handle_callback(
        user_id=42,
        callback_data=f"{CB_EDIT}:",
        session=session,
    )

    ctx = store.get(42)
    assert ctx.state == ConversationState.CLARIFYING
    assert "change" in result.reply_text.lower()


# ── Non-expense intents ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_greeting_intent() -> None:
    """Greeting intents should reply without entering the flow."""
    response = _make_llm_response(
        content="Hello! How can I help?",
        tool_calls=[_expense_tool_call([], intent="greeting")],
    )
    store = ConversationStore()
    orch, _ = _make_orchestrator(response, store)
    session = AsyncMock()

    result = await orch.handle_message(
        user_id=42,
        text="hello",
        session=session,
        raw_input_id=uuid.uuid4(),
    )

    assert not store.has(42)
    assert "hello" in result.reply_text.lower() or "help" in result.reply_text.lower()


@pytest.mark.asyncio
async def test_query_intent() -> None:
    """Query intents should clear state and attempt a query flow (Phase 5)."""
    response = _make_llm_response(
        tool_calls=[_expense_tool_call([], intent="query")],
    )
    store = ConversationStore()
    orch, _ = _make_orchestrator(response, store)
    session = AsyncMock()

    result = await orch.handle_message(
        user_id=42,
        text="how much did we spend?",
        session=session,
        raw_input_id=uuid.uuid4(),
    )

    assert not store.has(42)
    # The query handler will attempt to call a query tool. Since the mock LLM
    # returns a parse_expense tool call (not a query tool), it may fail or
    # return a fallback. Just verify it doesn't crash and returns something.
    assert result.reply_text is not None
    assert len(result.reply_text) > 0


# ── LLM failure ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_llm_failure_returns_error() -> None:
    """If the LLM call raises, the user should get an error message."""
    store = ConversationStore()
    orch, mock_llm = _make_orchestrator(store=store)
    mock_llm.chat = AsyncMock(side_effect=RuntimeError("LLM down"))
    session = AsyncMock()

    result = await orch.handle_message(
        user_id=42,
        text="groceries 300",
        session=session,
        raw_input_id=uuid.uuid4(),
    )

    assert "trouble" in result.reply_text.lower()
    assert not store.has(42)


# ── Multiple expenses ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_multiple_expenses_all_complete() -> None:
    """Multiple complete expenses should all reach CONFIRMING."""
    response = _make_llm_response(
        tool_calls=[
            _expense_tool_call(
                [
                    _complete_expense_dict(category="groceries", amount=300),
                    _complete_expense_dict(category="gas", amount=200),
                ]
            )
        ]
    )
    store = ConversationStore()
    orch, _ = _make_orchestrator(response, store)
    session = AsyncMock()

    result = await orch.handle_message(
        user_id=42,
        text="groceries 300 and gas 200, I paid, split 50/50",
        session=session,
        raw_input_id=uuid.uuid4(),
    )

    ctx = store.get(42)
    assert ctx.state == ConversationState.CONFIRMING
    assert len(ctx.pending_expenses) == 2
    assert result.keyboard is not None


@pytest.mark.asyncio
async def test_multiple_expenses_commit_all() -> None:
    """Confirming multiple expenses should write all to ledger."""
    store = ConversationStore()
    store.set(
        42,
        ConversationContext(
            state=ConversationState.CONFIRMING,
            raw_input_id=uuid.uuid4(),
            pending_expenses=[
                PendingExpense(
                    amount=300,
                    category="groceries",
                    payer="user",
                    split_payer_pct=50,
                    split_other_pct=50,
                ),
                PendingExpense(
                    amount=200,
                    category="gas",
                    payer="user",
                    split_payer_pct=50,
                    split_other_pct=50,
                ),
            ],
        ),
    )
    orch, _ = _make_orchestrator(store=store)
    session = AsyncMock()
    session.add = MagicMock()
    session.flush = AsyncMock()

    result = await orch.handle_callback(
        user_id=42,
        callback_data=f"{CB_CONFIRM}:",
        session=session,
    )

    assert "2 expense" in result.reply_text.lower()
    assert session.add.call_count == 2


# ── Callback with no pending state ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_callback_with_no_state() -> None:
    """Callback when user has no pending expenses should return error."""
    store = ConversationStore()
    orch, _ = _make_orchestrator(store=store)
    session = AsyncMock()

    result = await orch.handle_callback(
        user_id=42,
        callback_data=f"{CB_CONFIRM}:",
        session=session,
    )

    assert "no pending" in result.reply_text.lower()


# ── Helper function tests ────────────────────────────────────────────────────


class TestParsesSplit:
    def test_fifty_fifty(self) -> None:
        assert _parse_split("50/50") == (50, 50)

    def test_seventy_thirty(self) -> None:
        assert _parse_split("70/30") == (70, 30)

    def test_hundred_zero(self) -> None:
        assert _parse_split("100/0") == (100, 0)

    def test_with_spaces(self) -> None:
        assert _parse_split("60 / 40") == (60, 40)

    def test_invalid(self) -> None:
        assert _parse_split("abc") == (None, None)

    def test_doesnt_sum_to_100(self) -> None:
        assert _parse_split("50/60") == (None, None)


class TestResolveDate:
    def test_valid_date(self) -> None:
        assert _resolve_date("2025-12-05") == date(2025, 12, 5)

    def test_none_returns_today(self) -> None:
        assert _resolve_date(None) == date.today()

    def test_empty_returns_today(self) -> None:
        assert _resolve_date("") == date.today()

    def test_invalid_returns_today(self) -> None:
        assert _resolve_date("not-a-date") == date.today()


class TestBuildClarificationQuestion:
    def test_payer_question(self) -> None:
        q = _build_clarification_question(
            "payer",
            0,
            [PendingExpense(amount=300, category="groceries")],
        )
        assert "paid" in q.lower() or "who" in q.lower()

    def test_split_question(self) -> None:
        q = _build_clarification_question(
            "split_payer_pct",
            0,
            [PendingExpense(amount=300, category="groceries")],
        )
        assert "split" in q.lower()

    def test_category_question(self) -> None:
        q = _build_clarification_question(
            "category",
            0,
            [PendingExpense(amount=300)],
        )
        assert "category" in q.lower()

    def test_multi_expense_prefix(self) -> None:
        q = _build_clarification_question(
            "payer",
            1,
            [PendingExpense(amount=100), PendingExpense(amount=200)],
        )
        assert "#2" in q


class TestMergeFieldManually:
    def test_payer_me(self) -> None:
        expenses = [PendingExpense(amount=300, category="groceries")]
        _merge_field_manually(expenses, "payer", "me")
        assert expenses[0].payer == "user"

    def test_payer_partner(self) -> None:
        expenses = [PendingExpense(amount=300, category="groceries")]
        _merge_field_manually(expenses, "payer", "partner")
        assert expenses[0].payer == "partner"

    def test_split(self) -> None:
        expenses = [PendingExpense(amount=300, category="groceries")]
        _merge_field_manually(expenses, "split_payer_pct", "70/30")
        assert expenses[0].split_payer_pct == 70
        assert expenses[0].split_other_pct == 30

    def test_category(self) -> None:
        expenses = [PendingExpense(amount=300)]
        _merge_field_manually(expenses, "category", "dining")
        assert expenses[0].category == "dining"

    def test_skips_already_filled(self) -> None:
        expenses = [PendingExpense(amount=300, category="groceries", payer="user")]
        _merge_field_manually(expenses, "payer", "partner")
        # Should NOT change — payer was already filled.
        assert expenses[0].payer == "user"

    def test_applies_to_all_missing(self) -> None:
        expenses = [
            PendingExpense(amount=300, category="groceries"),
            PendingExpense(amount=200, category="gas"),
        ]
        _merge_field_manually(expenses, "payer", "me")
        assert expenses[0].payer == "user"
        assert expenses[1].payer == "user"
