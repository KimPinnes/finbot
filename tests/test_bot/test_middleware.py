"""Tests for bot middleware (access control and DB session injection)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finbot.bot.middleware import AccessControlMiddleware, DbSessionMiddleware

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_update(user_id: int = 111) -> MagicMock:
    """Create a minimal mock of an aiogram ``Update`` with a message."""
    from aiogram.types import Update

    update = MagicMock(spec=Update)
    update.message = MagicMock()
    update.message.from_user = MagicMock()
    update.message.from_user.id = user_id
    update.callback_query = None
    return update


# ── AccessControlMiddleware tests ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_acl_allows_when_no_allowlist() -> None:
    """When allowed_telegram_user_ids is empty, all users pass through."""
    mw = AccessControlMiddleware()
    handler = AsyncMock(return_value="ok")
    update = _make_update(user_id=999)

    with patch("finbot.bot.middleware.settings") as mock_settings:
        mock_settings.allowed_telegram_user_ids = []
        result = await mw(handler, update, {})

    assert result == "ok"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_acl_allows_authorized_user() -> None:
    """An authorized user should pass through the middleware."""
    mw = AccessControlMiddleware()
    handler = AsyncMock(return_value="ok")
    update = _make_update(user_id=42)

    with patch("finbot.bot.middleware.settings") as mock_settings:
        mock_settings.allowed_telegram_user_ids = [42, 99]
        result = await mw(handler, update, {})

    assert result == "ok"
    handler.assert_called_once()


@pytest.mark.asyncio
async def test_acl_rejects_unauthorized_user() -> None:
    """An unauthorized user should be silently rejected."""
    mw = AccessControlMiddleware()
    handler = AsyncMock(return_value="ok")
    update = _make_update(user_id=666)

    with patch("finbot.bot.middleware.settings") as mock_settings:
        mock_settings.allowed_telegram_user_ids = [42, 99]
        result = await mw(handler, update, {})

    assert result is None
    handler.assert_not_called()


@pytest.mark.asyncio
async def test_acl_handles_callback_query() -> None:
    """Access control should also work for callback queries."""
    from aiogram.types import Update

    mw = AccessControlMiddleware()
    handler = AsyncMock(return_value="ok")

    update = MagicMock(spec=Update)
    update.message = None
    update.callback_query = MagicMock()
    update.callback_query.from_user = MagicMock()
    update.callback_query.from_user.id = 42

    with patch("finbot.bot.middleware.settings") as mock_settings:
        mock_settings.allowed_telegram_user_ids = [42]
        result = await mw(handler, update, {})

    assert result == "ok"
    handler.assert_called_once()


# ── DbSessionMiddleware tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_db_session_middleware_injects_session() -> None:
    """Handler data should receive a 'session' key from the middleware."""
    mw = DbSessionMiddleware()
    captured_data: dict = {}

    async def handler(event, data):
        captured_data.update(data)
        return "ok"

    fake_session = AsyncMock()

    with patch("finbot.bot.middleware.get_session") as mock_get:
        # Simulate the async context manager
        ctx = AsyncMock()
        ctx.__aenter__ = AsyncMock(return_value=fake_session)
        ctx.__aexit__ = AsyncMock(return_value=False)
        mock_get.return_value = ctx

        result = await mw(handler, MagicMock(), {})

    assert result == "ok"
    assert captured_data.get("session") is fake_session
