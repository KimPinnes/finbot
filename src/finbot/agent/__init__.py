"""LLM agent orchestration layer.

Provides entry points for the bot handler layer:

- :func:`process_message` — send a user text message through the
  multi-step orchestrator (parse → validate → clarify → confirm).
- :func:`process_callback` — handle inline keyboard button presses
  (confirm / edit / cancel).

Both functions return an :class:`~finbot.agent.orchestrator.OrchestratorResult`.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from finbot.agent.llm_client import (
    FallbackLLMClient,
    LLMClient,
)
from finbot.agent.orchestrator import Orchestrator, OrchestratorResult

logger = logging.getLogger(__name__)

# Module-level LLM client — lazily initialized.
_llm_client: LLMClient | None = None

# Module-level orchestrator — lazily initialized.
_orchestrator: Orchestrator | None = None


def get_llm_client() -> LLMClient:
    """Return the module-level LLM client, creating it on first call."""
    global _llm_client
    if _llm_client is None:
        _llm_client = FallbackLLMClient()
    return _llm_client


def set_llm_client(client: LLMClient) -> None:
    """Override the module-level LLM client (useful for testing)."""
    global _llm_client, _orchestrator
    _llm_client = client
    # Reset orchestrator so it picks up the new client.
    _orchestrator = None


def get_orchestrator() -> Orchestrator:
    """Return the module-level orchestrator, creating it on first call."""
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = Orchestrator(llm_client=get_llm_client())
    return _orchestrator


def set_orchestrator(orch: Orchestrator) -> None:
    """Override the module-level orchestrator (useful for testing)."""
    global _orchestrator
    _orchestrator = orch


async def process_message(
    user_id: int,
    text: str,
    session: AsyncSession,
    raw_input_id: uuid.UUID,
) -> OrchestratorResult:
    """Send a user message through the multi-step orchestrator.

    This is the primary entry point called by the bot text handler.

    Args:
        user_id: Telegram user ID.
        text: The raw message text from the user.
        session: Active async database session.
        raw_input_id: UUID of the persisted ``raw_inputs`` row.

    Returns:
        An :class:`OrchestratorResult` with reply text, optional keyboard,
        and optional message ID to edit.
    """
    orch = get_orchestrator()
    return await orch.handle_message(
        user_id=user_id,
        text=text,
        session=session,
        raw_input_id=raw_input_id,
    )


async def process_callback(
    user_id: int,
    callback_data: str,
    session: AsyncSession,
) -> OrchestratorResult:
    """Handle an inline keyboard callback (Confirm / Edit / Cancel).

    Args:
        user_id: Telegram user ID.
        callback_data: The callback string (e.g. ``"confirm:abc"``).
        session: Active async database session.

    Returns:
        An :class:`OrchestratorResult` with reply text and optional edit info.
    """
    orch = get_orchestrator()
    return await orch.handle_callback(
        user_id=user_id,
        callback_data=callback_data,
        session=session,
    )
