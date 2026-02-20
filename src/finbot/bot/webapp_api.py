"""Lightweight aiohttp server for Mini App expense submissions.

Telegram's ``sendData()`` JS method only works when the Mini App is
opened from a ``KeyboardButton`` (reply keyboard).  On desktop clients
and when opened from ``InlineKeyboardButton`` or ``MenuButtonWebApp``,
it silently fails.

This module provides an HTTP POST endpoint that the Mini App can call
instead, bypassing ``sendData()`` entirely.  The endpoint validates the
Telegram ``initData`` hash, then processes the expense exactly as the
``handle_webapp_data`` handler does.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from urllib.parse import parse_qs

from aiohttp import web

from finbot.config import settings

logger = logging.getLogger(__name__)


def _validate_init_data(init_data: str, bot_token: str) -> dict | None:
    """Validate Telegram Mini App ``initData`` hash.

    Returns the parsed data dict if valid, or ``None`` if the hash
    check fails.  See https://core.telegram.org/bots/webapps#validating-data
    """
    if not init_data:
        return None

    parsed = parse_qs(init_data, keep_blank_values=True)
    received_hash = parsed.pop("hash", [None])[0]
    if not received_hash:
        return None

    data_pairs = []
    for key in sorted(parsed):
        data_pairs.append(f"{key}={parsed[key][0]}")
    data_check_string = "\n".join(data_pairs)

    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()

    if not hmac.compare_digest(computed, received_hash):
        return None

    user_raw = parsed.get("user", [None])[0]
    if user_raw:
        try:
            parsed["user"] = [json.loads(user_raw)]
        except json.JSONDecodeError:
            pass

    return {k: v[0] for k, v in parsed.items()}


def _cors_headers() -> dict[str, str]:
    return {
        "Access-Control-Allow-Origin": "*",
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
    }


async def handle_expense_options(request: web.Request) -> web.Response:
    """OPTIONS /api/expense — CORS preflight."""
    return web.Response(status=204, headers=_cors_headers())


async def handle_expense_submit(request: web.Request) -> web.Response:
    """POST /api/expense — receive expense data from the Mini App."""
    from finbot.agent.state import (
        ConversationContext,
        ConversationState,
        PendingExpense,
        conversation_store,
    )
    from finbot.bot.formatters import format_confirmation_summary
    from finbot.bot.keyboards import confirmation_keyboard
    from finbot.db.session import get_session
    from finbot.ledger.repository import save_raw_input

    cors = _cors_headers()

    try:
        body = await request.json()
    except (json.JSONDecodeError, Exception):
        return web.json_response({"ok": False, "error": "Invalid JSON"}, status=400, headers=cors)

    init_data = body.get("initData", "")
    expense_data = body.get("expense")
    if not expense_data:
        return web.json_response({"ok": False, "error": "Missing expense data"}, status=400, headers=cors)

    validated = _validate_init_data(init_data, settings.telegram_bot_token)
    if not validated:
        logger.warning("Mini App submit with invalid initData")
        return web.json_response({"ok": False, "error": "Invalid auth"}, status=403, headers=cors)

    user_info = validated.get("user")
    if isinstance(user_info, str):
        try:
            user_info = json.loads(user_info)
        except json.JSONDecodeError:
            user_info = None

    if not user_info or not isinstance(user_info, dict):
        return web.json_response({"ok": False, "error": "No user in initData"}, status=403, headers=cors)

    user_id = user_info.get("id")
    if not user_id:
        return web.json_response({"ok": False, "error": "No user ID"}, status=403, headers=cors)
    user_id = int(user_id)

    allowed = settings.allowed_telegram_user_ids
    if allowed and user_id not in allowed:
        logger.warning("Rejected Mini App submit from unauthorized user %s", user_id)
        return web.json_response({"ok": False, "error": "Unauthorized"}, status=403, headers=cors)

    logger.info("Mini App submit from user %s: %s", user_id, expense_data)

    expense = PendingExpense.from_parsed(expense_data)
    if not expense.is_complete():
        missing = ", ".join(expense.missing_fields())
        return web.json_response({"ok": False, "error": f"Missing fields: {missing}"}, status=400, headers=cors)

    bot = request.app["bot"]

    async with get_session() as session:
        raw_input = await save_raw_input(
            session=session,
            telegram_user_id=user_id,
            raw_text=f"[webapp] {json.dumps(expense_data)}",
        )

    ctx = ConversationContext(
        state=ConversationState.CONFIRMING,
        raw_input_id=raw_input.id,
        pending_expenses=[expense],
        original_text=json.dumps(expense_data),
    )
    conversation_store.set(user_id, ctx)

    summary = format_confirmation_summary([expense])
    sent = await bot.send_message(
        chat_id=user_id,
        text=summary,
        reply_markup=confirmation_keyboard(),
        parse_mode="HTML",
    )
    ctx.confirmation_message_id = sent.message_id
    conversation_store.set(user_id, ctx)

    return web.json_response({"ok": True}, headers=cors)


def create_webapp_server(bot) -> web.Application:
    """Build the aiohttp application for the Mini App API.

    Also serves static files from the ``webapp/`` directory so
    the Mini App and API share the same origin (no CORS needed).
    """
    import pathlib

    app = web.Application()
    app["bot"] = bot

    app.router.add_options("/api/expense", handle_expense_options)
    app.router.add_post("/api/expense", handle_expense_submit)

    webapp_dir = pathlib.Path(__file__).resolve().parents[3] / "webapp"
    if webapp_dir.is_dir():
        app.router.add_static("/", webapp_dir, show_index=True)
        logger.info("Serving Mini App from %s", webapp_dir)

    return app
