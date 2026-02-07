"""Entry point for ``python -m finbot``."""

import asyncio

from finbot.bot import run_bot


def main() -> None:
    """Launch the FinBot Telegram bot."""
    asyncio.run(run_bot())


if __name__ == "__main__":
    main()
