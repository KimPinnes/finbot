"""Conversation state models and in-memory store for the agent orchestrator.

Provides:

- :class:`PendingExpense` — a single expense being built up through the
  multi-step conversation flow (fields start ``None`` and are populated as
  the user provides information or the LLM infers values).
- :class:`ConversationState` — enum of states in the agent state machine.
- :class:`ConversationContext` — the full per-user conversation context
  (current state, pending expenses, which field is being clarified, etc.).
- :class:`ConversationStore` — in-memory dict-based store keyed by Telegram
  user ID.  Acceptable for the 2-user MVP; if the bot restarts, users
  simply re-send their message.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

# ── Conversation states ───────────────────────────────────────────────────────


class ConversationState(StrEnum):
    """States in the agent orchestrator state machine."""

    IDLE = "idle"
    PARSING = "parsing"
    VALIDATING = "validating"
    CLARIFYING = "clarifying"
    CONFIRMING = "confirming"
    COMMITTING = "committing"


# ── Pending expense model ─────────────────────────────────────────────────────

# Fields that must be non-None before an expense can be committed.
REQUIRED_EXPENSE_FIELDS: list[str] = [
    "amount",
    "category",
    "payer",
    "split_payer_pct",
    "split_other_pct",
]


class PendingExpense(BaseModel):
    """A single expense being assembled through the conversation flow.

    Fields start as ``None`` (except defaults) and are populated as the LLM
    parses the user's input and clarification answers fill in gaps.
    """

    amount: float | None = Field(
        default=None,
        description="Expense amount as a positive number.",
    )
    currency: str = Field(
        default="ILS",
        description="Three-letter currency code.",
    )
    category: str | None = Field(
        default=None,
        description="Expense category (e.g. 'groceries', 'gas').",
    )
    description: str | None = Field(
        default=None,
        description="Brief description of the expense.",
    )
    payer: str | None = Field(
        default=None,
        description="Who paid: 'user' or 'partner'.",
    )
    split_payer_pct: float | None = Field(
        default=None,
        description="Payer's share as a percentage (0-100).",
    )
    split_other_pct: float | None = Field(
        default=None,
        description="Other partner's share as a percentage (0-100).",
    )
    event_date: str | None = Field(
        default=None,
        description="Date in YYYY-MM-DD format.  None means today.",
    )
    notes: list[str] = Field(
        default_factory=list,
        description="Notes about any automatic corrections or assumptions.",
    )

    def missing_fields(self) -> list[str]:
        """Return a list of required field names that are still ``None``."""
        missing: list[str] = []
        for field_name in REQUIRED_EXPENSE_FIELDS:
            if getattr(self, field_name) is None:
                missing.append(field_name)
        return missing

    def is_complete(self) -> bool:
        """Return ``True`` if all required fields have values."""
        return len(self.missing_fields()) == 0

    @classmethod
    def from_parsed(cls, data: dict[str, Any]) -> PendingExpense:
        """Build a :class:`PendingExpense` from an LLM-parsed dict.

        Unknown keys are silently ignored so the LLM can return extra fields
        without breaking things.
        """
        known_fields = cls.model_fields.keys()
        filtered = {k: v for k, v in data.items() if k in known_fields}
        return cls(**filtered)


# ── Conversation context ──────────────────────────────────────────────────────


class ConversationContext(BaseModel):
    """Full per-user conversation context carried between messages."""

    state: ConversationState = ConversationState.IDLE

    #: UUID of the ``raw_inputs`` row for the original message.
    raw_input_id: uuid.UUID | None = None

    #: Expenses being assembled — may have missing fields.
    pending_expenses: list[PendingExpense] = Field(default_factory=list)

    #: Which field we are currently asking the user about.
    clarification_field: str | None = None

    #: The Telegram message ID of the confirmation message (so we can edit it
    #: in response to callback queries).
    confirmation_message_id: int | None = None

    #: Original raw text that started this conversation round.
    original_text: str | None = None

    #: Whether the current pending items are settlements (Phase 5).
    is_settlement: bool = False

    #: Category being renamed (set by /categories flow).
    #: When not None, the next text message is treated as the new name.
    renaming_category: str | None = None

    def all_complete(self) -> bool:
        """Return ``True`` if every pending expense has all required fields."""
        return all(exp.is_complete() for exp in self.pending_expenses)

    def first_missing(self) -> tuple[int, str] | None:
        """Return ``(expense_index, field_name)`` for the first missing field.

        Returns ``None`` if everything is complete.
        """
        for i, exp in enumerate(self.pending_expenses):
            missing = exp.missing_fields()
            if missing:
                return (i, missing[0])
        return None


# ── In-memory conversation store ──────────────────────────────────────────────


class ConversationStore:
    """Dict-based in-memory store keyed by Telegram user ID.

    Thread-safety is not required — the bot runs on a single asyncio event
    loop and processes one update per user at a time.
    """

    def __init__(self) -> None:
        self._store: dict[int, ConversationContext] = {}

    def get(self, user_id: int) -> ConversationContext:
        """Return the context for *user_id*, creating a fresh one if absent."""
        if user_id not in self._store:
            self._store[user_id] = ConversationContext()
        return self._store[user_id]

    def set(self, user_id: int, ctx: ConversationContext) -> None:
        """Store *ctx* for *user_id*."""
        self._store[user_id] = ctx

    def clear(self, user_id: int) -> None:
        """Remove the context for *user_id*, resetting them to IDLE."""
        self._store.pop(user_id, None)

    def has(self, user_id: int) -> bool:
        """Return ``True`` if *user_id* has an active context."""
        return user_id in self._store


# ── Module-level singleton ────────────────────────────────────────────────────

conversation_store = ConversationStore()
