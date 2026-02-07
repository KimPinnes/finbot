"""Tests for settlement validation rules."""

from __future__ import annotations

from decimal import Decimal

from finbot.ledger.validation import validate_settlement

USER_A = 100
USER_B = 200


class TestValidateSettlement:
    """Tests for the validate_settlement function."""

    def test_valid_settlement_no_balance(self) -> None:
        """A valid settlement with no balance context passes."""
        errors = validate_settlement(
            amount=Decimal("500"),
            payer_telegram_id=USER_A,
            user_a_id=USER_A,
            user_b_id=USER_B,
        )
        assert errors == []

    def test_valid_settlement_with_balance(self) -> None:
        """A valid settlement within the outstanding balance passes."""
        errors = validate_settlement(
            amount=Decimal("100"),
            payer_telegram_id=USER_B,
            user_a_id=USER_A,
            user_b_id=USER_B,
            current_balance=Decimal("200"),  # B owes A 200
        )
        # No hard errors, no warnings (100 <= 200).
        hard = [e for e in errors if not e.startswith("WARNING:")]
        assert hard == []
        warnings = [e for e in errors if e.startswith("WARNING:")]
        assert warnings == []

    def test_zero_amount_fails(self) -> None:
        """Zero amount should be rejected."""
        errors = validate_settlement(
            amount=Decimal("0"),
            payer_telegram_id=USER_A,
            user_a_id=USER_A,
            user_b_id=USER_B,
        )
        assert any("positive" in e.lower() for e in errors)

    def test_negative_amount_fails(self) -> None:
        """Negative amount should be rejected."""
        errors = validate_settlement(
            amount=Decimal("-50"),
            payer_telegram_id=USER_A,
            user_a_id=USER_A,
            user_b_id=USER_B,
        )
        assert any("positive" in e.lower() for e in errors)

    def test_payer_not_in_partnership_fails(self) -> None:
        """Payer not matching either partner should be rejected."""
        errors = validate_settlement(
            amount=Decimal("100"),
            payer_telegram_id=999,
            user_a_id=USER_A,
            user_b_id=USER_B,
        )
        assert any("not one of the partners" in e.lower() for e in errors)

    def test_same_partners_fails(self) -> None:
        """Partners with the same ID should be rejected."""
        errors = validate_settlement(
            amount=Decimal("100"),
            payer_telegram_id=USER_A,
            user_a_id=USER_A,
            user_b_id=USER_A,
        )
        assert any("different" in e.lower() for e in errors)

    def test_overpayment_warning(self) -> None:
        """Amount exceeding the balance should produce a warning."""
        errors = validate_settlement(
            amount=Decimal("300"),
            payer_telegram_id=USER_B,
            user_a_id=USER_A,
            user_b_id=USER_B,
            current_balance=Decimal("100"),  # B owes A 100
        )
        hard = [e for e in errors if not e.startswith("WARNING:")]
        warnings = [e for e in errors if e.startswith("WARNING:")]
        assert hard == []
        assert len(warnings) == 1
        assert "exceeds" in warnings[0].lower()

    def test_payer_owes_nothing_warning(self) -> None:
        """Paying when you owe nothing should produce a warning."""
        errors = validate_settlement(
            amount=Decimal("100"),
            payer_telegram_id=USER_A,
            user_a_id=USER_A,
            user_b_id=USER_B,
            current_balance=Decimal("50"),  # B owes A 50 (A owes nothing)
        )
        warnings = [e for e in errors if e.startswith("WARNING:")]
        assert len(warnings) == 1
        assert "does not currently owe" in warnings[0].lower()

    def test_payer_b_owes_nothing_warning(self) -> None:
        """User B paying when they owe nothing should warn."""
        errors = validate_settlement(
            amount=Decimal("100"),
            payer_telegram_id=USER_B,
            user_a_id=USER_A,
            user_b_id=USER_B,
            current_balance=Decimal("-50"),  # A owes B 50 (B owes nothing)
        )
        warnings = [e for e in errors if e.startswith("WARNING:")]
        assert len(warnings) == 1
        assert "does not currently owe" in warnings[0].lower()

    def test_exact_settlement_no_warning(self) -> None:
        """Settling the exact amount owed should produce no warnings."""
        errors = validate_settlement(
            amount=Decimal("200"),
            payer_telegram_id=USER_B,
            user_a_id=USER_A,
            user_b_id=USER_B,
            current_balance=Decimal("200"),  # B owes A exactly 200
        )
        assert errors == []
