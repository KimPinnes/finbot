"""Settlement tools for the LLM agent.

Provides tools for recording and validating settlements between partners:

- ``log_settlement`` — commit a validated settlement to the ledger
- ``validate_settlement`` — check settlement constraints before committing
"""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.ledger.balance import get_balance as _derive_balance
from finbot.ledger.repository import (
    get_partnership,
    get_partner_id,
    save_ledger_entry,
)
from finbot.ledger.validation import validate_settlement as _validate
from finbot.tools.registry import default_registry


@default_registry.tool(
    name="log_settlement",
    description=(
        "Record a settlement payment between partners. A settlement is a "
        "direct payment from one partner to the other to reduce or clear "
        "the outstanding balance."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "Settlement amount as a positive number.",
            },
            "payer": {
                "type": "string",
                "enum": ["user", "partner"],
                "description": "Who is paying: 'user' (message sender) or 'partner'.",
            },
            "description": {
                "type": "string",
                "description": "Optional description of the settlement.",
            },
            "event_date": {
                "type": "string",
                "description": "Date in YYYY-MM-DD format. Omit for today.",
            },
        },
        "required": ["amount", "payer"],
    },
)
async def log_settlement(
    *,
    amount: float,
    payer: str,
    description: str | None = None,
    event_date: str | None = None,
    session: AsyncSession | None = None,
    user_id: int | None = None,
    raw_input_id: uuid.UUID | None = None,
) -> dict:
    """Commit a settlement to the ledger.

    Args:
        amount: Settlement amount (positive).
        payer: ``"user"`` or ``"partner"``.
        description: Optional description.
        event_date: Date string (YYYY-MM-DD), defaults to today.
        session: Async database session.
        user_id: Telegram user ID of the requesting user.
        raw_input_id: UUID of the originating raw_inputs row.

    Returns:
        A dict with the result of the settlement logging.
    """
    if session is None or user_id is None or raw_input_id is None:
        return {
            "error": "Settlement logging requires a database session, user context, and raw input ID.",
        }

    partnership = await get_partnership(session, user_id)
    if partnership is None:
        return {"error": "No partnership found. Please set up a partnership first."}

    partner_id = get_partner_id(partnership, user_id)
    payer_id = user_id if payer == "user" else partner_id
    dec_amount = Decimal(str(amount))

    # Validate.
    current_balance = await _derive_balance(session, user_id, partner_id)
    errors = _validate(
        amount=dec_amount,
        payer_telegram_id=payer_id,
        user_a_id=user_id,
        user_b_id=partner_id,
        current_balance=current_balance,
    )

    # Separate hard errors from warnings.
    hard_errors = [e for e in errors if not e.startswith("WARNING:")]
    warnings = [e for e in errors if e.startswith("WARNING:")]

    if hard_errors:
        return {"error": " ".join(hard_errors), "warnings": warnings}

    # Parse date.
    settlement_date = date.today()
    if event_date:
        try:
            from datetime import datetime

            settlement_date = datetime.strptime(event_date, "%Y-%m-%d").date()
        except ValueError:
            pass

    # Settlements are recorded with 100/0 split — the full amount is
    # a direct payment from one partner to the other.
    entry = await save_ledger_entry(
        session,
        raw_input_id=raw_input_id,
        event_type="settlement",
        amount=dec_amount,
        currency=partnership.default_currency,
        category=None,
        payer_telegram_id=payer_id,
        split_payer_pct=Decimal("100"),
        split_other_pct=Decimal("0"),
        event_date=settlement_date,
        description=description or "Settlement payment",
    )

    payer_label = "You" if payer == "user" else "Partner"
    return {
        "success": True,
        "entry_id": str(entry.id),
        "description": (
            f"{payer_label} paid {partnership.default_currency} {dec_amount} "
            f"as a settlement."
        ),
        "warnings": warnings,
    }


@default_registry.tool(
    name="validate_settlement",
    description=(
        "Check whether a proposed settlement is valid before committing it. "
        "Returns validation errors and warnings."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "amount": {
                "type": "number",
                "description": "Settlement amount to validate.",
            },
            "payer": {
                "type": "string",
                "enum": ["user", "partner"],
                "description": "Who is paying: 'user' or 'partner'.",
            },
        },
        "required": ["amount", "payer"],
    },
)
async def validate_settlement_tool(
    *,
    amount: float,
    payer: str,
    session: AsyncSession | None = None,
    user_id: int | None = None,
) -> dict:
    """Check settlement validity without committing.

    Args:
        amount: Proposed settlement amount.
        payer: ``"user"`` or ``"partner"``.
        session: Async database session.
        user_id: Telegram user ID.

    Returns:
        A dict with ``valid`` (bool), ``errors``, and ``warnings`` keys.
    """
    if session is None or user_id is None:
        return {"error": "Validation requires a database session and user context."}

    partnership = await get_partnership(session, user_id)
    if partnership is None:
        return {"error": "No partnership found."}

    partner_id = get_partner_id(partnership, user_id)
    payer_id = user_id if payer == "user" else partner_id
    dec_amount = Decimal(str(amount))

    current_balance = await _derive_balance(session, user_id, partner_id)
    errors = _validate(
        amount=dec_amount,
        payer_telegram_id=payer_id,
        user_a_id=user_id,
        user_b_id=partner_id,
        current_balance=current_balance,
    )

    hard_errors = [e for e in errors if not e.startswith("WARNING:")]
    warnings = [e for e in errors if e.startswith("WARNING:")]

    return {
        "valid": len(hard_errors) == 0,
        "errors": hard_errors,
        "warnings": warnings,
        "current_balance": str(current_balance),
    }
