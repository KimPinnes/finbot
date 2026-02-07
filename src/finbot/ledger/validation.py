"""Settlement validation rules.

Provides :func:`validate_settlement` which checks whether a proposed
settlement is valid before it is committed to the ledger.

Validation errors are returned as a list of human-readable strings.
An empty list means the settlement is valid.
"""

from __future__ import annotations

from decimal import Decimal


def validate_settlement(
    amount: Decimal,
    payer_telegram_id: int,
    user_a_id: int,
    user_b_id: int,
    current_balance: Decimal | None = None,
) -> list[str]:
    """Validate a proposed settlement between two partners.

    Args:
        amount: The settlement amount (must be positive).
        payer_telegram_id: Telegram user ID of the person paying.
        user_a_id: Telegram user ID of the first partner.
        user_b_id: Telegram user ID of the second partner.
        current_balance: The current net balance (positive = user_b owes
            user_a).  If provided, an overpayment warning is emitted but
            the settlement is still allowed.

    Returns:
        A list of validation error/warning strings.  Empty means valid.
        Strings starting with ``"WARNING:"`` are soft warnings — the
        settlement can still proceed.
    """
    errors: list[str] = []

    # Amount must be positive.
    if amount <= 0:
        errors.append("Settlement amount must be a positive number.")

    # Payer must be one of the two partners.
    if payer_telegram_id not in (user_a_id, user_b_id):
        errors.append(
            f"Payer ({payer_telegram_id}) is not one of the partners ({user_a_id}, {user_b_id})."
        )

    # Partners must be distinct.
    if user_a_id == user_b_id:
        errors.append("The two partner IDs must be different.")

    # Overpayment warning (soft — does not block the settlement).
    if current_balance is not None and amount > 0:
        _check_overpayment(errors, amount, payer_telegram_id, user_a_id, current_balance)

    return errors


def _check_overpayment(
    errors: list[str],
    amount: Decimal,
    payer_telegram_id: int,
    user_a_id: int,
    balance: Decimal,
) -> None:
    """Append a warning if the settlement exceeds what the payer owes.

    The balance convention is: positive = user_b owes user_a.

    - If payer is user_a (balance is negative, meaning user_a owes user_b),
      the outstanding debt is ``abs(balance)``.
    - If payer is user_b (balance is positive, meaning user_b owes user_a),
      the outstanding debt is ``balance``.
    """
    if payer_telegram_id == user_a_id:
        # user_a is paying.  user_a owes something only if balance < 0.
        debt = abs(balance) if balance < 0 else Decimal("0")
    else:
        # user_b is paying.  user_b owes something only if balance > 0.
        debt = balance if balance > 0 else Decimal("0")

    if debt == 0:
        errors.append(
            f"WARNING: The payer does not currently owe anything. "
            f"This settlement of {amount} will create a credit."
        )
    elif amount > debt:
        errors.append(
            f"WARNING: Settlement amount ({amount}) exceeds the "
            f"outstanding balance ({debt}). The difference will "
            f"become a credit."
        )
