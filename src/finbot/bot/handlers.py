"""Telegram message and command handlers.

Defines an aiogram :class:`Router` with:

- ``/start`` — welcome message and brief instructions
- ``/help``  — usage guide
- ``/balance`` — show current balance between partners
- ``/setup``  — create a partnership with another Telegram user
- General text handler — persists the raw message, sends through the
  multi-step orchestrator, and replies with the result
- Callback query handler — processes inline keyboard button presses
  (Confirm / Edit / Cancel) from the confirmation flow
"""

from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.agent import process_callback, process_message
from finbot.agent.llm_client import LLMResponse, _estimate_cost_usd
from finbot.agent.orchestrator import OrchestratorResult
from finbot.ledger.repository import save_llm_call, save_raw_input

logger = logging.getLogger(__name__)

router = Router(name="main")

# ── Commands ──────────────────────────────────────────────────────────────────

WELCOME_TEXT = (
    "<b>Welcome to FinBot!</b>\n\n"
    "I help you and your partner track shared expenses.\n\n"
    "Just send me a message describing an expense, settlement, or query — "
    "for example:\n"
    '  <i>"groceries 300, I paid, split 50/50"</i>\n'
    '  <i>"how much do we owe each other?"</i>\n\n'
    "Type /help for more details."
)

HELP_TEXT = (
    "<b>How to use FinBot</b>\n\n"
    "<u>Log an expense</u>\n"
    'Send a message like: <i>"coffee 25, I paid"</i>\n'
    "I'll ask for any missing details (category, split, date) before "
    "committing.\n\n"
    "<u>Log a settlement</u>\n"
    '<i>"I paid partner 500"</i> or <i>"settled up 500"</i>\n\n'
    "<u>Check balance</u>\n"
    "Use /balance or ask: <i>\"what's the balance?\"</i>\n\n"
    "<u>Query expenses</u>\n"
    '<i>"how much did we spend on groceries this month?"</i>\n\n'
    "<u>Commands</u>\n"
    "/start — Show welcome message\n"
    "/help — Show this help text\n"
    "/balance — Show current balance\n"
    "/setup &lt;partner_id&gt; — Link with your partner (one-time setup)"
)


@router.message(Command("start"))
async def cmd_start(message: Message) -> None:
    """Handle the /start command with a welcome message."""
    await message.answer(WELCOME_TEXT)


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Handle the /help command with usage instructions."""
    await message.answer(HELP_TEXT)


@router.message(Command("balance"))
async def cmd_balance(message: Message, session: AsyncSession) -> None:
    """Handle the /balance command — show the current balance."""
    if not message.from_user:
        return

    user_id = message.from_user.id

    from finbot.bot.formatters import format_balance
    from finbot.ledger.balance import get_balance
    from finbot.ledger.repository import get_partner_id, get_partnership

    partnership = await get_partnership(session, user_id)
    if partnership is None:
        await message.answer(
            "<i>No partnership found. You need a partner set up to check the balance.</i>"
        )
        return

    partner_id = get_partner_id(partnership, user_id)
    balance = await get_balance(session, user_id, partner_id)
    currency = partnership.default_currency

    formatted = format_balance(balance, currency=currency, creditor_name="Partner")
    await message.answer(formatted)


@router.message(Command("setup"))
async def cmd_setup(message: Message, session: AsyncSession) -> None:
    """Handle the /setup command — create a partnership with another user.

    Usage: ``/setup <partner_telegram_id>``

    Validates that:
    - The sender provided a numeric partner ID
    - The partner ID differs from the sender's own ID
    - The partner ID is in the allowed user list (if configured)
    - Neither user already belongs to a partnership
    """
    if not message.from_user:
        return

    user_id = message.from_user.id
    args = (message.text or "").split()

    if len(args) < 2:
        await message.answer(
            "<b>Usage:</b> /setup &lt;partner_telegram_id&gt;\n\n"
            "Example: <code>/setup 987654321</code>\n\n"
            "You can find your Telegram user ID by messaging @userinfobot."
        )
        return

    # Parse partner ID.
    try:
        partner_id = int(args[1])
    except ValueError:
        await message.answer(
            "Invalid partner ID. Please provide a numeric Telegram user ID.\n"
            "Example: <code>/setup 987654321</code>"
        )
        return

    # Validate: can't partner with yourself.
    if partner_id == user_id:
        await message.answer("You can't create a partnership with yourself.")
        return

    # Validate: partner should be in the allowed list (if configured).
    from finbot.config import settings

    allowed_ids = settings.allowed_telegram_user_ids
    if allowed_ids and partner_id not in allowed_ids:
        await message.answer(
            f"User <code>{partner_id}</code> is not in the allowed users list.\n"
            "Both partners must be listed in <code>ALLOWED_TELEGRAM_USER_IDS</code>."
        )
        return

    # Check for existing partnership / create new one.
    from finbot.ledger.repository import get_partner_id, save_partnership

    partnership, created = await save_partnership(
        session, user_id, partner_id,
    )

    if created:
        await message.answer(
            f"Partnership created between you and <code>{partner_id}</code>.\n"
            "You're all set! Start logging expenses."
        )
    else:
        existing_partner = get_partner_id(partnership, user_id)
        await message.answer(
            f"A partnership already exists (partner: <code>{existing_partner}</code>).\n"
            "Each user can only have one active partnership."
        )


# ── General text handler ──────────────────────────────────────────────────────


@router.message(F.text)
async def handle_text(message: Message, session: AsyncSession) -> None:
    """Process a free-text message through the multi-step orchestrator.

    1. Persist the raw message to ``raw_inputs`` for audit / reprocessing.
    2. Send through the orchestrator (parse → validate → clarify/confirm).
    3. Log any LLM calls to ``llm_calls`` (ADR-006).
    4. Reply with the orchestrator's result (text + optional keyboard).
    """
    if not message.from_user or not message.text:
        return

    user_id = message.from_user.id

    raw_input = await save_raw_input(
        session=session,
        telegram_user_id=user_id,
        raw_text=message.text,
    )

    logger.info(
        "Saved raw_input %s from user %s",
        raw_input.id,
        user_id,
    )

    # Send through the multi-step orchestrator.
    result: OrchestratorResult = await process_message(
        user_id=user_id,
        text=message.text,
        session=session,
        raw_input_id=raw_input.id,
    )

    # Log any LLM calls from this step.
    await _log_llm_responses(session, result)

    # Reply with text + optional inline keyboard.
    sent = await message.answer(
        result.reply_text,
        reply_markup=result.keyboard,
    )

    # Store the confirmation message ID so callbacks can edit it later.
    if result.keyboard is not None:
        from finbot.agent.state import conversation_store

        ctx = conversation_store.get(user_id)
        ctx.confirmation_message_id = sent.message_id
        conversation_store.set(user_id, ctx)


# ── Callback query handler ───────────────────────────────────────────────────


@router.callback_query()
async def handle_callback(
    callback_query: CallbackQuery, session: AsyncSession,
) -> None:
    """Process inline keyboard callbacks (Confirm / Edit / Cancel).

    Delegates to the orchestrator's ``handle_callback()`` and either edits
    the original confirmation message or sends a new reply.
    """
    if not callback_query.from_user or not callback_query.data:
        await callback_query.answer()
        return

    user_id = callback_query.from_user.id
    callback_data = callback_query.data

    logger.info(
        "Callback from user %s: %s",
        user_id,
        callback_data,
    )

    result: OrchestratorResult = await process_callback(
        user_id=user_id,
        callback_data=callback_data,
        session=session,
    )

    # Log any LLM calls from this step.
    await _log_llm_responses(session, result)

    # Acknowledge the callback to remove the loading indicator.
    await callback_query.answer()

    # If we have an edit_message_id, try editing the original message.
    if result.edit_message_id and callback_query.message:
        try:
            await callback_query.message.edit_text(
                result.reply_text,
                reply_markup=result.keyboard,
            )
            return
        except Exception:
            logger.debug("Could not edit message, sending new one instead")

    # Otherwise, send a new message.
    if callback_query.message:
        # Remove the keyboard from the original message.
        try:
            await callback_query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass  # Best effort.

        sent = await callback_query.message.answer(
            result.reply_text,
            reply_markup=result.keyboard,
        )

        # Store new confirmation message ID if a keyboard was sent.
        if result.keyboard is not None:
            from finbot.agent.state import conversation_store

            ctx = conversation_store.get(user_id)
            ctx.confirmation_message_id = sent.message_id
            conversation_store.set(user_id, ctx)


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _log_llm_responses(
    session: AsyncSession, result: OrchestratorResult,
) -> None:
    """Log all LLM calls embedded in an orchestrator result (ADR-006)."""
    for llm_response in result.llm_responses:
        if llm_response is None:
            continue
        is_fallback = "fallback" in (llm_response.provider or "")
        provider = llm_response.provider.replace(" (fallback)", "")
        await save_llm_call(
            session,
            provider=provider,
            model=llm_response.model,
            input_tokens=llm_response.input_tokens,
            output_tokens=llm_response.output_tokens,
            latency_ms=llm_response.latency_ms,
            is_fallback=is_fallback,
            fallback_reason=None,
            cost_usd=_estimate_cost_usd(
                provider, llm_response.model,
                llm_response.input_tokens, llm_response.output_tokens,
            ),
        )
