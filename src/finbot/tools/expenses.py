"""Expense parsing tool for the LLM agent.

Provides the ``parse_expense`` tool which extracts structured expense data
from free-text user messages.  This is a **read-only** extraction step —
the data is returned for validation and confirmation before being committed
to the ledger (that happens in Phase 4).

The :class:`ParsedExpense` Pydantic model defines the expected structure
and is used both for validation and for generating the JSON schema that
the LLM receives.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from finbot.tools.registry import default_registry


class ParsedExpense(BaseModel):
    """A single expense extracted from natural language.

    All fields that the LLM could not confidently determine are left as
    ``None`` — the agent orchestrator (Phase 4) will use these gaps to
    drive clarification questions.
    """

    amount: float = Field(..., description="Expense amount as a positive number.")
    currency: str = Field(
        default="ILS",
        description="Three-letter currency code (default: ILS).",
    )
    category: str | None = Field(
        default=None,
        description="Expense category (e.g. 'groceries', 'gas', 'coffee').",
    )
    description: str | None = Field(
        default=None,
        description="Brief description of the expense.",
    )
    payer: str | None = Field(
        default=None,
        description=(
            "Who paid: 'user' (the message sender) or 'partner'. "
            "None if not specified in the text."
        ),
    )
    split_payer_pct: float | None = Field(
        default=None,
        description="Payer's share as a percentage (0-100). E.g. 50 for 50/50 split.",
    )
    split_other_pct: float | None = Field(
        default=None,
        description="Other partner's share as a percentage (0-100).",
    )
    event_date: str | None = Field(
        default=None,
        description=(
            "Date of the expense in YYYY-MM-DD format. "
            "None means today (default)."
        ),
    )


class ParseExpenseResult(BaseModel):
    """Result of the parse_expense tool."""

    expenses: list[ParsedExpense] = Field(
        default_factory=list,
        description="List of expenses extracted from the text.",
    )
    intent: str = Field(
        default="expense",
        description=(
            "Detected intent: 'expense', 'settlement', 'query', "
            "'greeting', or 'unknown'."
        ),
    )
    raw_text: str = Field(
        default="",
        description="The original text that was parsed.",
    )


# ── JSON Schema for LLM tool calling ─────────────────────────────────────────

PARSE_EXPENSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "expenses": {
            "type": "array",
            "description": "List of expenses extracted from the user's message.",
            "items": {
                "type": "object",
                "properties": {
                    "amount": {
                        "type": "number",
                        "description": "Expense amount as a positive number.",
                    },
                    "currency": {
                        "type": "string",
                        "description": "Three-letter currency code (default: ILS).",
                        "default": "ILS",
                    },
                    "category": {
                        "type": "string",
                        "description": "Expense category (e.g. 'groceries', 'gas', 'coffee').",
                    },
                    "description": {
                        "type": "string",
                        "description": "Brief description of the expense.",
                    },
                    "payer": {
                        "type": "string",
                        "enum": ["user", "partner"],
                        "description": "Who paid: 'user' (message sender) or 'partner'.",
                    },
                    "split_payer_pct": {
                        "type": "number",
                        "description": "Payer's share as a percentage (0-100).",
                    },
                    "split_other_pct": {
                        "type": "number",
                        "description": "Other partner's share as a percentage (0-100).",
                    },
                    "event_date": {
                        "type": "string",
                        "description": "Date in YYYY-MM-DD format. Omit for today.",
                    },
                },
                "required": ["amount"],
            },
        },
        "intent": {
            "type": "string",
            "enum": ["expense", "settlement", "query", "greeting", "unknown"],
            "description": (
                "Detected intent of the user's message."
            ),
        },
        "raw_text": {
            "type": "string",
            "description": "Original user message text (optional).",
        },
    },
    "required": ["expenses", "intent"],
}


@default_registry.tool(
    name="parse_expense",
    description=(
        "Extract structured expense data from a natural language message. "
        "Returns a list of parsed expenses with amounts, categories, payer, "
        "split percentages, and dates. Also classifies the user's intent."
    ),
    parameters_schema=PARSE_EXPENSE_SCHEMA,
)
async def parse_expense(
    expenses: list[dict],
    intent: str,
    raw_text: str | None = None,
) -> dict:
    """Parse expense data from free text.

    This is a **pass-through** tool — in practice the LLM itself performs
    the extraction and returns the structured data via the tool call's
    arguments. This handler simply echoes the input back.

    The actual parsing flow is:
    1. The agent sends the user's text + system prompt to the LLM
    2. The LLM calls ``parse_expense`` with structured arguments
    3. This handler validates and returns the parsed data

    In Phase 3, this tool just returns a placeholder.  The real integration
    happens when the agent orchestrator (Phase 4) wires LLM output through
    this tool.

    Args:
        expenses: Parsed expenses from the LLM tool call.
        intent: Parsed intent label.
        raw_text: Optional original user text.

    Returns:
        A dict with ``raw_text`` set — actual parsing is done by the LLM,
        and the structured result will come from the LLM's tool call arguments.
    """
    return {
        "expenses": expenses,
        "intent": intent,
        "raw_text": raw_text or "",
    }
