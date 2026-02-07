"""Telegram bot factory and entry point.

Creates the aiogram :class:`Bot` and :class:`Dispatcher`, registers routers
and middleware, and exposes :func:`run_bot` to start long-polling.
"""

from __future__ import annotations

import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from finbot.bot.handlers import router as main_router
from finbot.bot.middleware import AccessControlMiddleware, DbSessionMiddleware
from finbot.config import settings
from finbot.db.session import engine, get_session
from finbot.ledger.repository import save_category

logger = logging.getLogger(__name__)


def create_dispatcher() -> Dispatcher:
    """Build and configure the aiogram Dispatcher.

    Registers routers and attaches middleware in the correct order:
    1. Access control (outermost — reject unauthorized users first)
    2. DB session injection (provides ``session`` to handlers)
    """
    dp = Dispatcher()

    # Outer middleware runs on the raw Update before routing.
    dp.update.outer_middleware(AccessControlMiddleware())

    # Message-level middleware — provides a DB session to handlers.
    dp.message.middleware(DbSessionMiddleware())

    # Callback query middleware — provides a DB session to callback handlers.
    dp.callback_query.middleware(DbSessionMiddleware())

    # Register handler routers.
    dp.include_router(main_router)

    return dp


def create_bot() -> Bot:
    """Create the aiogram Bot instance with default properties."""
    return Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )


async def _seed_default_categories() -> None:
    """Insert default categories from settings into the DB.

    Uses :func:`save_category` which skips existing rows, so this is safe
    to call on every startup.
    """
    async with get_session() as session:
        for name in settings.default_categories:
            await save_category(session, name)
    logger.info(
        "Seeded %d default categories",
        len(settings.default_categories),
    )


async def run_bot() -> None:
    """Start the Telegram bot with long-polling.

    This is the main coroutine invoked from ``__main__.py``.
    It sets up logging, creates the bot and dispatcher, and polls
    for updates until interrupted.
    """
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    bot = create_bot()
    dp = create_dispatcher()

    @dp.startup.register
    async def on_startup() -> None:
        logger.info("FinBot started — polling for updates")
        # Seed default categories into the DB (idempotent).
        await _seed_default_categories()

    @dp.shutdown.register
    async def on_shutdown() -> None:
        logger.info("FinBot shutting down — disposing DB engine")
        await engine.dispose()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
