"""Telegram bot factory and entry point.

Creates the aiogram :class:`Bot` and :class:`Dispatcher`, registers routers
and middleware, and exposes :func:`run_bot` to start long-polling.

Also starts a lightweight aiohttp server on ``WEBAPP_PORT`` (default 8080)
that serves the Mini App static files and a POST ``/api/expense`` endpoint
so the Mini App can submit expenses without relying on ``sendData()``.
"""

from __future__ import annotations

import logging

from aiohttp import web
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from sqlalchemy import select

from aiogram.types import BotCommand, MenuButtonWebApp, WebAppInfo

from finbot.bot.handlers import router as main_router
from finbot.bot.middleware import AccessControlMiddleware, DbSessionMiddleware
from finbot.bot.webapp_api import create_webapp_server
from finbot.config import settings
from finbot.db.session import engine, get_session
from finbot.ledger.models import Category
from finbot.ledger.repository import save_category

logger = logging.getLogger(__name__)

WEBAPP_PORT = 8080


async def _set_menu_button_webapp(bot: Bot) -> None:
    """Set the bot menu button to open the Mini App with categories in the URL."""
    if not settings.webapp_base_url:
        return
    async with get_session() as session:
        result = await session.execute(select(Category.name).order_by(Category.name))
        categories = list(result.scalars().all())
    if not categories:
        return
    base = settings.webapp_base_url.rstrip("/") + "/"
    url = f"{base}?cats={','.join(categories)}&currency={settings.default_currency}"
    if settings.webapp_api_url:
        url += f"&api={settings.webapp_api_url.rstrip('/')}"
    await bot.set_chat_menu_button(
        menu_button=MenuButtonWebApp(text="Add Expense", web_app=WebAppInfo(url=url)),
    )
    logger.info("Set menu button to Mini App with %d categories", len(categories))


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
        # Register bot commands so they appear in Telegram's "/" menu.
        await bot.set_my_commands([
            BotCommand(command="add", description="Add an expense via Mini App"),
            BotCommand(command="start", description="Show welcome message"),
            BotCommand(command="help", description="Show usage guide"),
            BotCommand(command="balance", description="Show current balance"),
            BotCommand(command="setup", description="Link with your partner"),
            BotCommand(command="categories", description="View and rename categories"),
        ])
        logger.info("Registered bot commands with Telegram")
        await _set_menu_button_webapp(bot)

    @dp.shutdown.register
    async def on_shutdown() -> None:
        logger.info("FinBot shutting down — disposing DB engine")
        await engine.dispose()

    webapp = create_webapp_server(bot)
    runner = web.AppRunner(webapp)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", WEBAPP_PORT)
    await site.start()
    logger.info("Mini App API server running on http://0.0.0.0:%d", WEBAPP_PORT)

    try:
        await dp.start_polling(bot)
    finally:
        await runner.cleanup()
        await bot.session.close()
