"""Aiogram middleware for access control and database session injection.

Middleware runs on every incoming update *before* it reaches a handler.

- :class:`AccessControlMiddleware` — restricts the bot to allowed Telegram
  user IDs (configured via ``ALLOWED_TELEGRAM_USER_IDS``).
- :class:`DbSessionMiddleware` — opens an async DB session per-update and
  injects it into handler data so handlers don't manage sessions directly.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject, Update

from finbot.config import settings
from finbot.db.session import get_session

logger = logging.getLogger(__name__)


class AccessControlMiddleware(BaseMiddleware):
    """Reject updates from users not in the allow-list.

    If ``settings.allowed_telegram_user_ids`` is empty, all users are
    permitted (useful during development).  Otherwise only listed IDs
    may interact with the bot.
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        # Extract user ID from the update, if present.
        user_id: int | None = None
        if isinstance(event, Update):
            if event.message and event.message.from_user:
                user_id = event.message.from_user.id
            elif event.callback_query and event.callback_query.from_user:
                user_id = event.callback_query.from_user.id

        allowed_ids = settings.allowed_telegram_user_ids

        # If the allow-list is configured and the user is not on it, reject.
        if allowed_ids and user_id not in allowed_ids:
            logger.warning("Rejected update from unauthorized user %s", user_id)
            return None

        return await handler(event, data)


class DbSessionMiddleware(BaseMiddleware):
    """Inject an async database session into handler data.

    The session is available to handlers via ``data["session"]``.
    It is automatically committed on success and rolled back on error
    (managed by :func:`finbot.db.session.get_session`).
    """

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        async with get_session() as session:
            data["session"] = session
            return await handler(event, data)
