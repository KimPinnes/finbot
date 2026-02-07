"""Inline keyboard builders for Telegram bot interactions.

Provides reusable keyboard layouts for the confirmation flow
(confirm / edit / cancel) used when committing expenses and settlements.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

# ── Callback data prefixes ────────────────────────────────────────────────────
# Each callback encodes an action and an entry identifier so the handler
# knows which pending entry the user is acting on.

CB_CONFIRM = "confirm"
CB_EDIT = "edit"
CB_CANCEL = "cancel"


def confirmation_keyboard(entry_id: str = "") -> InlineKeyboardMarkup:
    """Build a Confirm / Edit / Cancel inline keyboard.

    Args:
        entry_id: Optional identifier appended to callback data so the
            handler can match the callback to a specific pending entry.

    Returns:
        An :class:`InlineKeyboardMarkup` with a single row of three buttons.
    """
    suffix = f":{entry_id}" if entry_id else ""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="\u2705 Confirm",
                    callback_data=f"{CB_CONFIRM}{suffix}",
                ),
                InlineKeyboardButton(
                    text="\u270f\ufe0f Edit",
                    callback_data=f"{CB_EDIT}{suffix}",
                ),
                InlineKeyboardButton(
                    text="\u274c Cancel",
                    callback_data=f"{CB_CANCEL}{suffix}",
                ),
            ]
        ]
    )
