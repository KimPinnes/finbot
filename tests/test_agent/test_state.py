"""Tests for conversation state models and in-memory store."""

from __future__ import annotations

from uuid import uuid4

from finbot.agent.state import (
    ConversationContext,
    ConversationState,
    ConversationStore,
    PendingExpense,
)

# ── PendingExpense ─────────────────────────────────────────────────────────────


class TestPendingExpense:
    def test_defaults(self) -> None:
        exp = PendingExpense()
        assert exp.amount is None
        assert exp.currency == "ILS"
        assert exp.payer is None
        assert exp.split_payer_pct is None

    def test_missing_fields_all(self) -> None:
        exp = PendingExpense()
        missing = exp.missing_fields()
        assert "amount" in missing
        assert "category" in missing
        assert "payer" in missing
        assert "split_payer_pct" in missing
        assert "split_other_pct" in missing

    def test_is_complete_false(self) -> None:
        exp = PendingExpense(amount=100)
        assert not exp.is_complete()

    def test_is_complete_true(self) -> None:
        exp = PendingExpense(
            amount=100,
            category="groceries",
            payer="user",
            split_payer_pct=50,
            split_other_pct=50,
        )
        assert exp.is_complete()
        assert exp.missing_fields() == []

    def test_from_parsed_filters_unknown(self) -> None:
        data = {
            "amount": 200,
            "category": "gas",
            "payer": "partner",
            "unknown_key": "ignored",
            "split_payer_pct": 70,
            "split_other_pct": 30,
        }
        exp = PendingExpense.from_parsed(data)
        assert exp.amount == 200
        assert exp.category == "gas"
        assert exp.payer == "partner"
        assert exp.split_payer_pct == 70
        assert exp.split_other_pct == 30

    def test_from_parsed_empty_dict(self) -> None:
        exp = PendingExpense.from_parsed({})
        assert exp.amount is None
        assert exp.currency == "ILS"

    def test_currency_default(self) -> None:
        exp = PendingExpense(
            amount=50, category="coffee", payer="user", split_payer_pct=50, split_other_pct=50
        )
        assert exp.currency == "ILS"
        assert exp.is_complete()


# ── ConversationContext ───────────────────────────────────────────────────────


class TestConversationContext:
    def test_defaults(self) -> None:
        ctx = ConversationContext()
        assert ctx.state == ConversationState.IDLE
        assert ctx.raw_input_id is None
        assert ctx.pending_expenses == []
        assert ctx.clarification_field is None
        assert ctx.confirmation_message_id is None

    def test_all_complete_empty(self) -> None:
        ctx = ConversationContext()
        assert ctx.all_complete()

    def test_all_complete_with_complete_expenses(self) -> None:
        ctx = ConversationContext(
            pending_expenses=[
                PendingExpense(
                    amount=100,
                    category="groceries",
                    payer="user",
                    split_payer_pct=50,
                    split_other_pct=50,
                ),
            ]
        )
        assert ctx.all_complete()

    def test_all_complete_with_incomplete_expenses(self) -> None:
        ctx = ConversationContext(
            pending_expenses=[
                PendingExpense(amount=100),
            ]
        )
        assert not ctx.all_complete()

    def test_first_missing_none_when_complete(self) -> None:
        ctx = ConversationContext(
            pending_expenses=[
                PendingExpense(
                    amount=100,
                    category="groceries",
                    payer="user",
                    split_payer_pct=50,
                    split_other_pct=50,
                ),
            ]
        )
        assert ctx.first_missing() is None

    def test_first_missing_returns_first(self) -> None:
        ctx = ConversationContext(
            pending_expenses=[
                PendingExpense(amount=100, category="groceries"),
            ]
        )
        result = ctx.first_missing()
        assert result is not None
        idx, field = result
        assert idx == 0
        assert field == "payer"

    def test_first_missing_skips_complete_expenses(self) -> None:
        ctx = ConversationContext(
            pending_expenses=[
                PendingExpense(
                    amount=100,
                    category="groceries",
                    payer="user",
                    split_payer_pct=50,
                    split_other_pct=50,
                ),
                PendingExpense(amount=200),
            ]
        )
        result = ctx.first_missing()
        assert result is not None
        idx, field = result
        assert idx == 1
        assert field == "category"


# ── ConversationStore ─────────────────────────────────────────────────────────


class TestConversationStore:
    def test_get_creates_fresh(self) -> None:
        store = ConversationStore()
        ctx = store.get(111)
        assert ctx.state == ConversationState.IDLE
        assert store.has(111)

    def test_set_and_get(self) -> None:
        store = ConversationStore()
        ctx = ConversationContext(
            state=ConversationState.CONFIRMING,
            raw_input_id=uuid4(),
        )
        store.set(222, ctx)
        retrieved = store.get(222)
        assert retrieved.state == ConversationState.CONFIRMING
        assert retrieved.raw_input_id == ctx.raw_input_id

    def test_clear(self) -> None:
        store = ConversationStore()
        store.get(333)  # creates entry
        assert store.has(333)
        store.clear(333)
        assert not store.has(333)

    def test_clear_nonexistent_is_safe(self) -> None:
        store = ConversationStore()
        store.clear(999)  # should not raise

    def test_has_false_for_unknown(self) -> None:
        store = ConversationStore()
        assert not store.has(444)

    def test_independent_users(self) -> None:
        store = ConversationStore()
        ctx_a = store.get(100)
        store.get(200)  # Initialize user 200 with default context
        ctx_a.state = ConversationState.CLARIFYING
        store.set(100, ctx_a)
        assert store.get(200).state == ConversationState.IDLE
