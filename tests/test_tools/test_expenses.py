"""Tests for the expense parsing tool and Pydantic models.

Tests cover:
- ParsedExpense model validation
- ParseExpenseResult model structure
- parse_expense tool registration in default registry
- PARSE_EXPENSE_SCHEMA structure
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from finbot.tools.expenses import (
    PARSE_EXPENSE_SCHEMA,
    ParsedExpense,
    ParseExpenseResult,
    parse_expense,
)

# ── ParsedExpense model tests ─────────────────────────────────────────────────


def test_parsed_expense_minimal() -> None:
    """ParsedExpense should accept just an amount (all else optional/default)."""
    exp = ParsedExpense(amount=300)
    assert exp.amount == 300
    assert exp.currency == "ILS"
    assert exp.category is None
    assert exp.payer is None
    assert exp.split_payer_pct is None
    assert exp.event_date is None


def test_parsed_expense_full() -> None:
    """ParsedExpense should accept all fields."""
    exp = ParsedExpense(
        amount=300,
        currency="USD",
        category="groceries",
        description="Weekly shopping",
        payer="user",
        split_payer_pct=70,
        split_other_pct=30,
        event_date="2025-12-15",
    )
    assert exp.amount == 300
    assert exp.currency == "USD"
    assert exp.category == "groceries"
    assert exp.description == "Weekly shopping"
    assert exp.payer == "user"
    assert exp.split_payer_pct == 70
    assert exp.split_other_pct == 30
    assert exp.event_date == "2025-12-15"


def test_parsed_expense_requires_amount() -> None:
    """ParsedExpense should reject missing amount."""
    with pytest.raises(ValidationError):
        ParsedExpense()  # type: ignore[call-arg]


def test_parsed_expense_amount_must_be_numeric() -> None:
    """ParsedExpense should reject non-numeric amount."""
    with pytest.raises(ValidationError):
        ParsedExpense(amount="not a number")  # type: ignore[arg-type]


# ── ParseExpenseResult model tests ────────────────────────────────────────────


def test_parse_expense_result_defaults() -> None:
    """ParseExpenseResult should have sensible defaults."""
    result = ParseExpenseResult()
    assert result.expenses == []
    assert result.intent == "expense"
    assert result.raw_text == ""


def test_parse_expense_result_with_expenses() -> None:
    """ParseExpenseResult should hold a list of ParsedExpense objects."""
    result = ParseExpenseResult(
        expenses=[
            ParsedExpense(amount=300, category="groceries"),
            ParsedExpense(amount=200, category="gas"),
        ],
        intent="expense",
        raw_text="groceries 300 and gas 200",
    )
    assert len(result.expenses) == 2
    assert result.expenses[0].amount == 300
    assert result.expenses[1].category == "gas"


# ── PARSE_EXPENSE_SCHEMA tests ───────────────────────────────────────────────


def test_schema_has_required_structure() -> None:
    """The JSON schema should have the expected top-level structure."""
    assert PARSE_EXPENSE_SCHEMA["type"] == "object"
    assert "expenses" in PARSE_EXPENSE_SCHEMA["properties"]
    assert "intent" in PARSE_EXPENSE_SCHEMA["properties"]
    assert "expenses" in PARSE_EXPENSE_SCHEMA["required"]
    assert "intent" in PARSE_EXPENSE_SCHEMA["required"]


def test_schema_expenses_is_array() -> None:
    expenses_schema = PARSE_EXPENSE_SCHEMA["properties"]["expenses"]
    assert expenses_schema["type"] == "array"
    assert "items" in expenses_schema


def test_schema_expense_item_has_amount() -> None:
    item_schema = PARSE_EXPENSE_SCHEMA["properties"]["expenses"]["items"]
    assert "amount" in item_schema["properties"]
    assert "amount" in item_schema["required"]


def test_schema_intent_enum() -> None:
    intent_schema = PARSE_EXPENSE_SCHEMA["properties"]["intent"]
    assert intent_schema["type"] == "string"
    assert "expense" in intent_schema["enum"]
    assert "settlement" in intent_schema["enum"]
    assert "query" in intent_schema["enum"]


# ── parse_expense tool tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_parse_expense_tool_returns_raw_text() -> None:
    """The parse_expense tool handler should echo back the raw text."""
    result = await parse_expense(text="coffee 25")
    assert result["raw_text"] == "coffee 25"
    assert "message" in result


# ── Default registry integration ──────────────────────────────────────────────


def test_parse_expense_registered_in_default_registry() -> None:
    """parse_expense should be registered in the default tool registry."""
    from finbot.tools.registry import default_registry

    tool = default_registry.get_tool("parse_expense")
    assert tool is not None
    assert tool.name == "parse_expense"
    assert "expense" in tool.description.lower()
