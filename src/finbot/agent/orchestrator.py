"""Multi-step agent orchestrator (state machine).

Implements the conversation flow described in design.md §4:

    IDLE → PARSING → VALIDATING → CLARIFYING → CONFIRMING → COMMITTING → IDLE

Two public entry points:

- :func:`handle_message` — for incoming text messages.
- :func:`handle_callback` — for inline keyboard button presses.

Both return an :class:`OrchestratorResult` that the bot handler layer
converts into a Telegram reply.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import openai
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.agent.llm_client import (
    ChatMessage,
    LLMClient,
    LLMResponse,
    ToolCall,
)
from finbot.agent.prompts import (
    CLARIFY_FIELD_PROMPT,
    MERGE_CLARIFICATION_PROMPT,
    PARSE_EXPENSE_PROMPT,
    PARSE_SETTLEMENT_PROMPT,
    QUERY_PROMPT,
    SYSTEM_PROMPT,
)
from finbot.agent.state import (
    ConversationContext,
    ConversationState,
    ConversationStore,
    PendingExpense,
    conversation_store,
)
from finbot.config import settings
from finbot.ledger.repository import (
    get_partner_id,
    get_partnership,
    save_ledger_entry,
)
from finbot.tools import default_registry

# Keyboard constants are duplicated here to avoid a circular import:
#   agent → orchestrator → bot.keyboards → bot.__init__ → bot.handlers → agent
# The canonical values live in finbot.bot.keyboards.
_CB_CONFIRM = "confirm"
_CB_EDIT = "edit"
_CB_CANCEL = "cancel"

logger = logging.getLogger(__name__)

# Message shown when LLM is unreachable due to invalid/missing API key or Ollama down.
_LLM_AUTH_REPLY = (
    "I can't reach the AI: the fallback API key is invalid or missing. "
    "Either start Ollama locally (ollama serve) or set a valid OPENAI_API_KEY or ANTHROPIC_API_KEY in your .env."
)


def _is_llm_auth_error(exc: BaseException) -> bool:
    """True if the exception looks like an LLM API auth error (401 / invalid key)."""
    msg = str(exc).lower()
    return "401" in msg or "invalid_api_key" in msg or "incorrect api key" in msg or "unauthorized" in msg


def _return_auth_error(store: ConversationStore, user_id: int) -> OrchestratorResult:
    """Clear conversation state and return a user-facing auth error result."""
    store.clear(user_id)
    logger.warning("LLM call failed: invalid or missing API key for fallback provider")
    return OrchestratorResult(reply_text=_LLM_AUTH_REPLY)


def _format_llm_response_for_log(response: LLMResponse, max_content_len: int = 500) -> str:
    """Format an LLM response for human-readable logging (no raw JSON)."""
    parts: list[str] = []
    if response.content and response.content.strip():
        text = response.content.strip()
        if len(text) > max_content_len:
            text = text[:max_content_len] + "..."
        parts.append(f"content: {text!r}")
    if response.tool_calls:
        for tc in response.tool_calls:
            if tc.name == "parse_expense" and isinstance(tc.arguments, dict):
                args = tc.arguments
                intent = args.get("intent", "?")
                expenses = args.get("expenses") or []
                summary = f"intent={intent}, expenses={len(expenses)}"
                if expenses:
                    first = expenses[0]
                    if isinstance(first, dict):
                        a = first.get("amount")
                        c = first.get("category")
                        p = first.get("payer")
                        summary += f" [first: amount={a}, category={c}, payer={p}]"
                parts.append(f"tool {tc.name}({summary})")
            else:
                parts.append(f"tool {tc.name}(...)")
    return " | ".join(parts) if parts else "(empty)"


# ── Result dataclass ──────────────────────────────────────────────────────────


@dataclass
class OrchestratorResult:
    """Value object returned by the orchestrator to the bot handler layer."""

    #: Text to send (or edit into an existing message).
    reply_text: str

    #: Optional inline keyboard to attach to the reply.
    keyboard: InlineKeyboardMarkup | None = None

    #: If set, the bot should *edit* this message rather than send a new one.
    edit_message_id: int | None = None

    #: The LLM response(s) generated during this step (for logging).
    llm_responses: list[LLMResponse] = field(default_factory=list)


# ── Orchestrator ──────────────────────────────────────────────────────────────


class Orchestrator:
    """Multi-step state machine for the agent conversation flow.

    Args:
        llm_client: The LLM client to use for chat completions.
        store: Conversation state store (defaults to the module-level singleton).
    """

    def __init__(
        self,
        llm_client: LLMClient,
        store: ConversationStore | None = None,
    ) -> None:
        self._llm = llm_client
        self._store = store or conversation_store

    # ── Public entry points ───────────────────────────────────────────────

    async def handle_message(
        self,
        user_id: int,
        text: str,
        session: AsyncSession,
        raw_input_id: uuid.UUID,
    ) -> OrchestratorResult:
        """Process an incoming text message through the state machine.

        Args:
            user_id: Telegram user ID.
            text: The raw message text.
            session: Active DB session for commits.
            raw_input_id: UUID of the persisted ``raw_inputs`` row.

        Returns:
            An :class:`OrchestratorResult` for the bot handler to send.
        """
        ctx = self._store.get(user_id)

        # If the user is in CLARIFYING state, this text is an answer to a
        # clarification question — merge it into the pending data.
        if ctx.state == ConversationState.CLARIFYING:
            return await self._handle_clarification_answer(
                user_id, text, ctx, session,
            )

        # Otherwise, start a fresh parsing round.
        ctx = ConversationContext(
            state=ConversationState.PARSING,
            raw_input_id=raw_input_id,
            original_text=text,
        )
        self._store.set(user_id, ctx)

        return await self._parse_and_validate(user_id, text, ctx, session)

    async def handle_callback(
        self,
        user_id: int,
        callback_data: str,
        session: AsyncSession,
    ) -> OrchestratorResult:
        """Process an inline keyboard callback (Confirm / Edit / Cancel).

        Args:
            user_id: Telegram user ID.
            callback_data: The callback string (e.g. ``"confirm:abc"``).
            session: Active DB session for commits.

        Returns:
            An :class:`OrchestratorResult` for the bot handler to send.
        """
        ctx = self._store.get(user_id)
        action = callback_data.split(":")[0]

        if ctx.state != ConversationState.CONFIRMING:
            return OrchestratorResult(
                reply_text="No pending expenses to act on. Send a new expense.",
            )

        if action == _CB_CONFIRM:
            return await self._commit(user_id, ctx, session)
        elif action == _CB_EDIT:
            return self._start_edit(user_id, ctx)
        elif action == _CB_CANCEL:
            return self._cancel(user_id)
        else:
            return OrchestratorResult(reply_text="Unknown action.")

    # ── Internal: parsing ─────────────────────────────────────────────────

    async def _parse_and_validate(
        self,
        user_id: int,
        text: str,
        ctx: ConversationContext,
        session: AsyncSession,
    ) -> OrchestratorResult:
        """Send the user's text to the LLM for parsing, then validate."""
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=PARSE_EXPENSE_PROMPT.format(
                    user_message=text,
                    current_date=str(date.today()),
                ),
            ),
        ]
        tools = default_registry.get_tools_for_llm()

        if _looks_like_settlement(text):
            initial = LLMResponse()
            return await self._handle_settlement(
                user_id, text, ctx, session, initial,
            )
        if _looks_like_query(text) and not re.search(r"\d", text):
            initial = LLMResponse()
            return await self._handle_query(user_id, text, session, initial)

        try:
            response = await self._llm.chat(messages=messages, tools=tools)
        except openai.AuthenticationError:
            return _return_auth_error(self._store, user_id)
        except Exception as e:
            if _is_llm_auth_error(e):
                return _return_auth_error(self._store, user_id)
            logger.exception("LLM call failed for message: %s", text[:100])
            self._store.clear(user_id)
            return OrchestratorResult(
                reply_text=(
                    "I'm having trouble processing your message right now. "
                    "Please try again in a moment."
                ),
            )

        logger.info(
            "LLM request: user_message=%s",
            text if len(text) <= 200 else text[:200] + "...",
        )
        logger.info("LLM response: %s", _format_llm_response_for_log(response))

        parsed = self._extract_parsed(response)
        if parsed and parsed.get("expenses"):
            parsed = _postprocess_parsed_expenses(text, parsed)

        # Handle non-expense intents (only when no expenses were extracted).
        has_expenses = parsed and parsed.get("expenses")
        if parsed is not None and not has_expenses:
            intent = parsed.get("intent", "unknown")
            if intent == "query" or (
                intent in ("unknown", "greeting") and _looks_like_query(text)
            ):
                self._store.clear(user_id)
                return await self._handle_query(user_id, text, session, response)
            if intent == "settlement":
                return await self._handle_settlement(
                    user_id, text, ctx, session, response,
                )
            if intent in ("greeting", "unknown"):
                self._store.clear(user_id)
                return self._handle_non_expense_intent(intent, response)

        # No expenses extracted.
        if parsed is None or not has_expenses:
            self._store.clear(user_id)
            # If LLM returned text content, use it.
            if response.content and response.content.strip():
                return OrchestratorResult(
                    reply_text=response.content,
                    llm_responses=[response],
                )
            return OrchestratorResult(
                reply_text=(
                    "I couldn't parse your message. Try something like:\n"
                    '<i>"groceries 300, I paid, split 50/50"</i>'
                ),
                llm_responses=[response],
            )

        # Build PendingExpense objects.
        ctx.pending_expenses = [
            PendingExpense.from_parsed(exp)
            for exp in parsed["expenses"]
        ]
        _default_missing_payers_to_user(ctx.pending_expenses)
        ctx.state = ConversationState.VALIDATING
        self._store.set(user_id, ctx)

        return self._validate(user_id, ctx, llm_response=response)

    def _extract_parsed(self, response: LLMResponse) -> dict[str, Any] | None:
        """Extract the parsed data dict from LLM tool calls."""
        if not response.tool_calls:
            return None
        for tc in response.tool_calls:
            if tc.name == "parse_expense":
                return tc.arguments
        # Fall back to first tool call.
        return response.tool_calls[0].arguments

    def _handle_non_expense_intent(
        self, intent: str, response: LLMResponse,
    ) -> OrchestratorResult:
        """Build a reply for non-expense intents (greeting / unknown)."""
        if intent == "greeting":
            text = (
                response.content
                or "Hello! Send me an expense to track."
            )
        else:
            text = (
                response.content
                or "I didn't understand that. Try describing an expense."
            )
        return OrchestratorResult(reply_text=text, llm_responses=[response])

    # ── Internal: query handling (Phase 5) ────────────────────────────────

    async def _handle_query(
        self,
        user_id: int,
        text: str,
        session: AsyncSession,
        initial_response: LLMResponse,
    ) -> OrchestratorResult:
        """Process a query intent by calling the appropriate query tool.

        Sends the user's message to the LLM with the QUERY_PROMPT, which
        instructs the LLM to call ``get_balance``, ``query_expenses``, or
        ``get_recent_entries``.  The tool result is then formatted and
        returned to the user.
        """
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=QUERY_PROMPT.format(
                    user_message=text,
                    current_date=str(date.today()),
                ),
            ),
        ]
        tools = default_registry.get_tools_for_llm()

        try:
            response = await self._llm.chat(messages=messages, tools=tools)
        except Exception:
            logger.exception("LLM query call failed for: %s", text[:100])
            return OrchestratorResult(
                reply_text=(
                    "I had trouble processing your query. "
                    "Please try again in a moment."
                ),
                llm_responses=[initial_response],
            )

        logger.info("LLM query request: user_message=%s", text if len(text) <= 200 else text[:200] + "...")
        logger.info("LLM query response: %s", _format_llm_response_for_log(response))

        all_responses = [initial_response, response]

        # Execute any tool calls the LLM made.
        if response.tool_calls:
            for tc in response.tool_calls:
                tool_name = tc.name
                tool_args = dict(tc.arguments)
                # Inject session and user context.
                tool_args["session"] = session
                tool_args["user_id"] = user_id

                try:
                    result = await default_registry.execute_tool(
                        tool_name, tool_args,
                    )
                except Exception:
                    logger.exception("Tool %s failed", tool_name)
                    result = {"error": f"Tool {tool_name} failed."}

                if isinstance(result, dict):
                    if "error" in result:
                        return OrchestratorResult(
                            reply_text=result["error"],
                            llm_responses=all_responses,
                        )
                    # Format the result.
                    from finbot.bot.formatters import format_query_result

                    formatted = format_query_result(result, tool_name)
                    return OrchestratorResult(
                        reply_text=formatted,
                        llm_responses=all_responses,
                    )

        # No tool calls — use LLM text response.
        text_reply = (
            response.content
            or "I couldn't find the information you're looking for. "
            "Try asking about your balance or expenses."
        )
        return OrchestratorResult(
            reply_text=text_reply,
            llm_responses=all_responses,
        )

    # ── Internal: settlement handling (Phase 5) ───────────────────────────

    async def _handle_settlement(
        self,
        user_id: int,
        text: str,
        ctx: ConversationContext,
        session: AsyncSession,
        initial_response: LLMResponse,
    ) -> OrchestratorResult:
        """Process a settlement intent.

        Sends the user's message to the LLM with the PARSE_SETTLEMENT_PROMPT
        to extract settlement details (amount, payer, date).  Then shows a
        confirmation before committing.
        """
        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=PARSE_SETTLEMENT_PROMPT.format(
                    user_message=text,
                    current_date=str(date.today()),
                ),
            ),
        ]
        tools = default_registry.get_tools_for_llm()

        try:
            response = await self._llm.chat(messages=messages, tools=tools)
        except Exception:
            logger.exception("LLM settlement parse failed: %s", text[:100])
            self._store.clear(user_id)
            return OrchestratorResult(
                reply_text=(
                    "I had trouble processing your settlement. "
                    "Please try again."
                ),
                llm_responses=[initial_response],
            )

        logger.info("LLM settlement request: user_message=%s", text if len(text) <= 200 else text[:200] + "...")
        logger.info("LLM settlement response: %s", _format_llm_response_for_log(response))

        all_responses = [initial_response, response]

        # Extract settlement data from tool calls.
        settlement_data = self._extract_settlement(response)

        if settlement_data is None:
            self._store.clear(user_id)
            return OrchestratorResult(
                reply_text=(
                    "I couldn't parse the settlement details. "
                    "Try something like:\n"
                    '<i>"I paid partner 500"</i> or '
                    '<i>"settled up 300"</i>'
                ),
                llm_responses=all_responses,
            )

        _postprocess_settlement(text, settlement_data)

        # Build a PendingExpense to re-use the confirmation flow.
        # Settlements use split 100/0 (full amount is the direct payment).
        amount = settlement_data.get("amount")
        payer = settlement_data.get("payer")

        if amount is None:
            self._store.clear(user_id)
            return OrchestratorResult(
                reply_text="How much is the settlement for?",
                llm_responses=all_responses,
            )

        # Store settlement info in context for confirmation.
        pending = PendingExpense(
            amount=float(amount),
            currency="ILS",
            category=None,
            description=settlement_data.get("description", "Settlement payment"),
            payer=payer,
            split_payer_pct=100.0,
            split_other_pct=0.0,
            event_date=settlement_data.get("event_date"),
        )

        ctx.pending_expenses = [pending]
        ctx.is_settlement = True

        # If payer is missing, ask.
        if not pending.payer:
            ctx.state = ConversationState.CLARIFYING
            ctx.clarification_field = "payer"
            self._store.set(user_id, ctx)
            return OrchestratorResult(
                reply_text="Who made this payment? You or your partner?",
                llm_responses=all_responses,
            )

        # All info present — show confirmation.
        from finbot.bot.formatters import format_settlement_confirmation
        from finbot.bot.keyboards import confirmation_keyboard

        ctx.state = ConversationState.CONFIRMING
        self._store.set(user_id, ctx)

        summary = format_settlement_confirmation(pending)
        return OrchestratorResult(
            reply_text=summary,
            keyboard=confirmation_keyboard(),
            llm_responses=all_responses,
        )

    def _extract_settlement(self, response: LLMResponse) -> dict[str, Any] | None:
        """Extract settlement data from LLM tool calls."""
        if not response.tool_calls:
            return None
        for tc in response.tool_calls:
            if tc.name == "log_settlement":
                return tc.arguments
        # Check parse_expense too — the LLM might use it.
        for tc in response.tool_calls:
            if tc.name == "parse_expense":
                args = tc.arguments
                if args.get("intent") == "settlement" and args.get("expenses"):
                    exp = args["expenses"][0]
                    return {
                        "amount": exp.get("amount"),
                        "payer": exp.get("payer"),
                        "description": exp.get("description"),
                        "event_date": exp.get("event_date"),
                    }
        return None

    # ── Internal: validation ──────────────────────────────────────────────

    def _validate(
        self,
        user_id: int,
        ctx: ConversationContext,
        llm_response: LLMResponse | None = None,
    ) -> OrchestratorResult:
        """Check required fields; move to CONFIRMING or CLARIFYING."""
        # Lazy imports to break circular dependency:
        #   agent → orchestrator → bot.* → bot.__init__ → bot.handlers → agent
        from finbot.bot.formatters import format_confirmation_summary
        from finbot.bot.keyboards import confirmation_keyboard

        responses = [llm_response] if llm_response else []

        if settings.assume_half_split:
            _apply_default_split(ctx)

        if ctx.all_complete():
            # All fields present — show confirmation.
            ctx.state = ConversationState.CONFIRMING
            self._store.set(user_id, ctx)
            summary = format_confirmation_summary(ctx.pending_expenses)
            return OrchestratorResult(
                reply_text=summary,
                keyboard=confirmation_keyboard(),
                llm_responses=responses,
            )

        # Some fields missing — ask about the first one.
        missing_info = ctx.first_missing()
        assert missing_info is not None
        idx, field_name = missing_info
        ctx.state = ConversationState.CLARIFYING
        ctx.clarification_field = field_name
        self._store.set(user_id, ctx)

        question = _build_clarification_question(
            field_name, idx, ctx.pending_expenses,
        )
        return OrchestratorResult(
            reply_text=question,
            llm_responses=responses,
        )

    # ── Internal: clarification ───────────────────────────────────────────

    async def _handle_clarification_answer(
        self,
        user_id: int,
        answer: str,
        ctx: ConversationContext,
        session: AsyncSession,
    ) -> OrchestratorResult:
        """Merge a clarification answer into the pending data and re-validate."""
        field_name = ctx.clarification_field or "unknown"

        # Build a summary of current parsed data for the LLM.
        parsed_summary = _expenses_to_summary(ctx.pending_expenses)

        messages = [
            ChatMessage(role="system", content=SYSTEM_PROMPT),
            ChatMessage(
                role="user",
                content=MERGE_CLARIFICATION_PROMPT.format(
                    parsed_summary=parsed_summary,
                    clarification_field=field_name,
                    user_answer=answer,
                ),
            ),
        ]
        tools = default_registry.get_tools_for_llm()

        try:
            response = await self._llm.chat(messages=messages, tools=tools)
        except Exception:
            logger.exception("LLM merge call failed for answer: %s", answer[:100])
            # Re-ask the same question.
            question = _build_clarification_question(
                field_name, 0, ctx.pending_expenses,
            )
            return OrchestratorResult(
                reply_text=(
                    "I had trouble processing your answer. "
                    f"Could you try again?\n\n{question}"
                ),
            )

        logger.info("LLM clarification merge request: field=%s, user_answer=%s", field_name, answer if len(answer) <= 200 else answer[:200] + "...")
        logger.info("LLM clarification merge response: %s", _format_llm_response_for_log(response))

        parsed = self._extract_parsed(response)

        if parsed and parsed.get("expenses"):
            # Update the pending expenses with merged data.
            new_expenses = [
                PendingExpense.from_parsed(exp) for exp in parsed["expenses"]
            ]
            # Preserve count: if LLM returns different count, keep original.
            if len(new_expenses) == len(ctx.pending_expenses):
                ctx.pending_expenses = new_expenses
            else:
                # LLM returned wrong count — try to merge field manually.
                _merge_field_manually(
                    ctx.pending_expenses, field_name, answer,
                )
        else:
            # LLM didn't return tool calls — try manual merge.
            _merge_field_manually(ctx.pending_expenses, field_name, answer)

        ctx.state = ConversationState.VALIDATING
        ctx.clarification_field = None
        self._store.set(user_id, ctx)

        return self._validate(user_id, ctx, llm_response=response)

    # ── Internal: commit ──────────────────────────────────────────────────

    async def _commit(
        self,
        user_id: int,
        ctx: ConversationContext,
        session: AsyncSession,
    ) -> OrchestratorResult:
        """Write all pending expenses/settlements to the ledger and reset state."""
        if not ctx.raw_input_id:
            self._store.clear(user_id)
            return OrchestratorResult(
                reply_text="Something went wrong — no input reference. Please try again.",
            )

        is_settlement = getattr(ctx, "is_settlement", False)
        event_type = "settlement" if is_settlement else "expense"

        committed: list[str] = []
        for exp in ctx.pending_expenses:
            if exp.amount is None:
                continue
            # For settlements, payer is always required but splits are fixed.
            if not is_settlement and not exp.is_complete():
                continue

            event_date = _resolve_date(exp.event_date)
            payer_tid = await _resolve_payer_id(
                exp.payer or "user", user_id, session,
            )

            await save_ledger_entry(
                session,
                raw_input_id=ctx.raw_input_id,
                event_type=event_type,
                amount=Decimal(str(exp.amount)),
                currency=exp.currency,
                category=exp.category,
                payer_telegram_id=payer_tid,
                split_payer_pct=Decimal(str(exp.split_payer_pct or 100)),
                split_other_pct=Decimal(str(exp.split_other_pct or 0)),
                event_date=event_date,
                description=exp.description,
            )
            label = exp.description or exp.category or event_type
            committed.append(
                f"  {exp.currency} {exp.amount} — {label}"
            )

        ctx.state = ConversationState.COMMITTING
        self._store.set(user_id, ctx)

        count = len(committed)
        lines = "\n".join(committed)

        if is_settlement:
            reply = (
                f"\u2705 <b>Settlement recorded:</b>\n"
                f"{lines}"
            )
        else:
            reply = (
                f"\u2705 <b>Committed {count} expense(s) to the ledger:</b>\n"
                f"{lines}"
            )

        # Reset to IDLE.
        self._store.clear(user_id)

        return OrchestratorResult(
            reply_text=reply,
            edit_message_id=ctx.confirmation_message_id,
        )

    # ── Internal: edit ────────────────────────────────────────────────────

    def _start_edit(
        self,
        user_id: int,
        ctx: ConversationContext,
    ) -> OrchestratorResult:
        """Prompt the user for what they want to change."""
        ctx.state = ConversationState.CLARIFYING
        ctx.clarification_field = None  # General edit — next text parsed as correction
        self._store.set(user_id, ctx)

        return OrchestratorResult(
            reply_text=(
                "What would you like to change? You can say things like:\n"
                '<i>"change the amount to 350"</i>\n'
                '<i>"the category is dining"</i>\n'
                '<i>"I paid, split 60/40"</i>'
            ),
            edit_message_id=ctx.confirmation_message_id,
        )

    # ── Internal: cancel ──────────────────────────────────────────────────

    def _cancel(self, user_id: int) -> OrchestratorResult:
        """Discard pending expenses and reset to IDLE."""
        ctx = self._store.get(user_id)
        edit_id = ctx.confirmation_message_id
        self._store.clear(user_id)
        return OrchestratorResult(
            reply_text="\u274c Cancelled. No expenses were recorded.",
            edit_message_id=edit_id,
        )


# ── Helper functions ──────────────────────────────────────────────────────────


def _build_clarification_question(
    field_name: str,
    expense_idx: int,
    expenses: list[PendingExpense],
) -> str:
    """Build a hardcoded clarification question for a missing field.

    Uses simple templates rather than an LLM call to keep the flow fast
    and deterministic.  The LLM-generated question path is available via
    CLARIFY_FIELD_PROMPT but reserved for complex cases.
    """
    # Build a context string about what we already know.
    exp = expenses[expense_idx] if expense_idx < len(expenses) else None
    context = ""
    if exp:
        parts: list[str] = []
        if exp.amount is not None:
            parts.append(f"{exp.currency} {exp.amount}")
        if exp.category:
            parts.append(exp.category)
        if parts:
            context = f" for <b>{' — '.join(parts)}</b>"

    multi = len(expenses) > 1
    prefix = f"For expense #{expense_idx + 1}" if multi else ""

    templates: dict[str, str] = {
        "payer": f"{prefix}Who paid{context}? You or your partner?".strip(),
        "category": f"{prefix}What category is this expense{context}? "
                    f"(e.g. groceries, gas, dining, coffee)".strip(),
        "split_payer_pct": (
            f"{prefix}How should this expense{context} be split? "
            f"(e.g. 50/50, 70/30, or 100/0)"
        ).strip(),
        "split_other_pct": (
            f"{prefix}How should this expense{context} be split? "
            f"(e.g. 50/50, 70/30, or 100/0)"
        ).strip(),
        "amount": f"{prefix}What was the amount{context}?".strip(),
    }

    return templates.get(
        field_name,
        f"Could you provide the {field_name}{context}?",
    )


def _expenses_to_summary(expenses: list[PendingExpense]) -> str:
    """Serialize pending expenses into a text summary for LLM prompts."""
    lines: list[str] = []
    for i, exp in enumerate(expenses, 1):
        parts: list[str] = [f"Expense {i}:"]
        parts.append(f"  amount: {exp.amount}")
        parts.append(f"  currency: {exp.currency}")
        parts.append(f"  category: {exp.category}")
        parts.append(f"  description: {exp.description}")
        parts.append(f"  payer: {exp.payer}")
        parts.append(f"  split_payer_pct: {exp.split_payer_pct}")
        parts.append(f"  split_other_pct: {exp.split_other_pct}")
        parts.append(f"  event_date: {exp.event_date}")
        lines.append("\n".join(parts))
    return "\n\n".join(lines)


def _merge_field_manually(
    expenses: list[PendingExpense],
    field_name: str,
    answer: str,
) -> None:
    """Best-effort manual merge when the LLM doesn't return tool calls.

    Applies the answer to ALL expenses that are missing the given field.
    """
    answer_stripped = answer.strip().lower()

    for exp in expenses:
        if getattr(exp, field_name, "NOT_MISSING") is not None:
            continue  # Field already filled.

        if field_name == "payer":
            if answer_stripped in ("me", "i", "i did", "i paid", "user"):
                exp.payer = "user"
            elif answer_stripped in ("partner", "they", "they did", "them"):
                exp.payer = "partner"
            else:
                exp.payer = "user"  # Default to user for ambiguous answers.

        elif field_name in ("split_payer_pct", "split_other_pct"):
            payer_pct, other_pct = _parse_split(answer_stripped)
            if payer_pct is not None:
                exp.split_payer_pct = payer_pct
                exp.split_other_pct = other_pct

        elif field_name == "category":
            exp.category = answer_stripped or None

        elif field_name == "amount":
            try:
                exp.amount = float(answer_stripped.replace(",", ""))
            except ValueError:
                pass  # Leave as None — will be re-asked.


def _parse_split(text: str) -> tuple[float | None, float | None]:
    """Parse a split specification like '50/50' or '70/30'."""
    text = text.replace(" ", "")
    if "/" in text:
        parts = text.split("/")
        if len(parts) == 2:
            try:
                a = float(parts[0])
                b = float(parts[1])
                if abs(a + b - 100) < 0.01:
                    return a, b
            except ValueError:
                pass
    # Try percentage-like answers.
    for word in text.split():
        try:
            pct = float(word.rstrip("%"))
            if 0 <= pct <= 100:
                return pct, 100 - pct
        except ValueError:
            continue
    return None, None


def _apply_default_split(ctx: ConversationContext) -> None:
    """Apply a default 50/50 split when both split fields are missing."""
    for exp in ctx.pending_expenses:
        if exp.split_payer_pct is None and exp.split_other_pct is None:
            exp.split_payer_pct = 50.0
            exp.split_other_pct = 50.0


def _default_missing_payers_to_user(expenses: list[PendingExpense]) -> None:
    """Default missing expense payers to the sender."""
    for exp in expenses:
        if exp.payer is None:
            exp.payer = "user"


def _extract_relative_date(text: str) -> date | None:
    """Extract a relative date (yesterday, N days ago, one week ago)."""
    lowered = text.lower()
    today = date.today()

    if "yesterday" in lowered:
        return today - timedelta(days=1)
    if "today" in lowered:
        return today
    if "tomorrow" in lowered:
        return today + timedelta(days=1)
    if "one week ago" in lowered or "a week ago" in lowered or "last week" in lowered:
        return today - timedelta(days=7)

    match = re.search(r"\b(\d+)\s+days?\s+ago\b", lowered)
    if match:
        return today - timedelta(days=int(match.group(1)))

    match = re.search(r"\b(\d+)\s+weeks?\s+ago\b", lowered)
    if match:
        return today - timedelta(days=int(match.group(1)) * 7)

    return None


def _extract_numeric_amounts(text: str) -> list[float]:
    """Extract numeric amounts from text, ignoring ISO dates."""
    cleaned = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    matches = re.findall(
        r"\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b|\b\d+(?:\.\d+)?\b",
        cleaned,
    )
    amounts: list[float] = []
    for m in matches:
        try:
            amounts.append(float(m.replace(",", "")))
        except ValueError:
            continue
    return amounts


def _maybe_fix_amount(
    parsed_amount: float | None,
    candidates: list[float],
    *,
    single_expense: bool,
) -> float | None:
    """Fix amount if the parsed value is obviously truncated."""
    if not candidates or not single_expense:
        return parsed_amount

    if parsed_amount is None:
        return candidates[0] if len(candidates) == 1 else parsed_amount

    if len(candidates) == 1:
        candidate = candidates[0]
        if candidate >= 1000 and parsed_amount < candidate * 0.5:
            return candidate
        if candidate < 1000 and parsed_amount > 0 and candidate / parsed_amount >= 5:
            return candidate
        return parsed_amount

    largest = max(candidates)
    if largest >= 1000 and parsed_amount < largest * 0.5:
        return largest

    return parsed_amount


def _should_override_event_date(
    current_value: str | None,
    relative_date: date,
) -> bool:
    """Decide whether to override a parsed date with a relative date."""
    if not current_value:
        return True
    try:
        parsed = datetime.strptime(current_value, "%Y-%m-%d").date()
    except ValueError:
        return True
    return abs((parsed - relative_date).days) > 366


def _postprocess_parsed_expenses(
    text: str,
    parsed: dict[str, Any],
) -> dict[str, Any]:
    """Apply deterministic fixes to LLM-parsed expenses."""
    expenses = parsed.get("expenses") or []
    if not expenses:
        return parsed

    candidates = _extract_numeric_amounts(text)
    rel_date = _extract_relative_date(text)
    single = len(expenses) == 1

    for exp in expenses:
        notes = exp.get("notes") or []
        amount = exp.get("amount")
        try:
            amount_val = float(amount) if amount is not None else None
        except (TypeError, ValueError):
            amount_val = None
        fixed_amount = _maybe_fix_amount(amount_val, candidates, single_expense=single)
        if fixed_amount is not None and fixed_amount != amount_val:
            exp["amount"] = fixed_amount
            if amount_val is not None:
                notes.append(
                    f"Amount auto-corrected from {amount_val:g} to {fixed_amount:g}"
                )

        if rel_date and _should_override_event_date(exp.get("event_date"), rel_date):
            exp["event_date"] = rel_date.isoformat()
            notes.append(f"Date auto-corrected to {rel_date.isoformat()}")

        if notes:
            exp["notes"] = notes

    parsed["expenses"] = expenses
    return parsed


def _postprocess_settlement(text: str, settlement_data: dict[str, Any]) -> None:
    """Apply deterministic fixes to settlement data."""
    candidates = _extract_numeric_amounts(text)
    rel_date = _extract_relative_date(text)
    notes = settlement_data.get("notes") or []

    amount = settlement_data.get("amount")
    try:
        amount_val = float(amount) if amount is not None else None
    except (TypeError, ValueError):
        amount_val = None
    fixed_amount = _maybe_fix_amount(amount_val, candidates, single_expense=True)
    if fixed_amount is not None and fixed_amount != amount_val:
        settlement_data["amount"] = fixed_amount
        if amount_val is not None:
            notes.append(
                f"Amount auto-corrected from {amount_val:g} to {fixed_amount:g}"
            )

    if rel_date and _should_override_event_date(settlement_data.get("event_date"), rel_date):
        settlement_data["event_date"] = rel_date.isoformat()
        notes.append(f"Date auto-corrected to {rel_date.isoformat()}")

    if notes:
        settlement_data["notes"] = notes


def _looks_like_settlement(text: str) -> bool:
    """Heuristic: detect partner-to-partner payments."""
    lowered = text.lower()
    if re.search(r"\bsettle|settled|settlement|settled up\b", lowered):
        return True
    if re.search(r"\bpaid\s+me\b", lowered) or re.search(r"\bsent\s+me\b", lowered):
        return True
    if re.search(r"\btransfer(?:red)?\b", lowered) or "reimbursed" in lowered:
        return True
    if re.search(r"\bto\s+me\b", lowered) or re.search(r"\bto\s+you\b", lowered):
        return True
    return False


def _looks_like_query(text: str) -> bool:
    """Heuristic: detect expense/balance queries."""
    lowered = text.lower()
    if re.search(r"\bbalance|owe|owed|settled\b", lowered):
        return True
    if re.search(r"\btotal|totals|summary|breakdown\b", lowered):
        return True
    if re.search(r"\bby\s+category|by\s+categories|per\s+category\b", lowered):
        return True
    if re.search(r"\bcategory\s+totals?\b", lowered):
        return True
    if re.search(r"\bspend|spent|expenses|expanses\b", lowered):
        return True
    if re.search(r"\brecent|last\s+few|latest\b", lowered):
        return True
    return False


def _resolve_date(date_str: str | None) -> date:
    """Convert a date string to a :class:`date` object, defaulting to today."""
    if not date_str:
        return date.today()
    try:
        return datetime.strptime(date_str, "%Y-%m-%d").date()
    except ValueError:
        return date.today()


async def _resolve_payer_id(
    payer: str,
    sender_user_id: int,
    session: AsyncSession,
) -> int:
    """Convert ``'user'`` / ``'partner'`` to a Telegram user ID.

    Looks up the partnership from the database to resolve the partner's
    Telegram user ID.  Falls back to ``0`` if no partnership is found.

    Args:
        payer: ``"user"`` or ``"partner"``.
        sender_user_id: Telegram user ID of the message sender.
        session: Active async database session.

    Returns:
        The resolved Telegram user ID.
    """
    if payer == "user":
        return sender_user_id

    # Look up the partner from the partnerships table.
    partnership = await get_partnership(session, sender_user_id)
    if partnership is not None:
        return get_partner_id(partnership, sender_user_id)

    # No partnership found — fall back to 0.
    logger.warning(
        "No partnership found for user %s; using 0 as partner ID",
        sender_user_id,
    )
    return 0
