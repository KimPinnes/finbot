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
CB_RENAME_CAT = "rencat:"


def categories_keyboard(categories: list[str]) -> InlineKeyboardMarkup:
    """Build an inline keyboard with one button per category for renaming.

    Each button sends callback data ``rencat:<category_name>`` so the
    handler can identify which category the user wants to rename.

    Args:
        categories: Sorted list of category name strings.

    Returns:
        An :class:`InlineKeyboardMarkup` with categories laid out in
        two-column rows.
    """
    buttons: list[list[InlineKeyboardButton]] = []
    row: list[InlineKeyboardButton] = []
    for name in categories:
        row.append(
            InlineKeyboardButton(
                text=name,
                callback_data=f"{CB_RENAME_CAT}{name}",
            )
        )
        if len(row) == 2:
            buttons.append(row)
            row = []
    if row:
        buttons.append(row)
    return InlineKeyboardMarkup(inline_keyboard=buttons)


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
