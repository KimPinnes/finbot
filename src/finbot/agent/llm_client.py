"""Abstract LLM client with Ollama primary and paid API fallback.

Defines a protocol-based interface for LLM communication with three
concrete implementations:

- :class:`OllamaLLMClient` — wraps the Ollama async client (primary, local)
- :class:`PaidLLMClient` — wraps Anthropic or OpenAI SDKs (fallback)
- :class:`FallbackLLMClient` — composite: tries Ollama first, falls back to paid API

Every LLM call is logged to the ``llm_calls`` table (see ADR-006).
"""

from __future__ import annotations

import logging
import time
from decimal import Decimal
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from finbot.config import settings

logger = logging.getLogger(__name__)


# ── Data models ───────────────────────────────────────────────────────────────


class ToolCall(BaseModel):
    """A single tool call requested by the LLM."""

    id: str = ""
    name: str
    arguments: dict[str, Any]


class LLMResponse(BaseModel):
    """Structured response from an LLM call."""

    content: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: int | None = None
    provider: str = ""
    model: str = ""


# ── Message types ─────────────────────────────────────────────────────────────


class ChatMessage(BaseModel):
    """A single message in a chat conversation."""

    role: str  # "system", "user", "assistant", "tool"
    content: str = ""
    tool_call_id: str | None = None
    tool_calls: list[ToolCall] | None = None


# ── Tool schema type (matches OpenAI function-calling format) ─────────────────

ToolSchema = dict[str, Any]
"""JSON-serializable tool schema in OpenAI function-calling format:

    {
        "type": "function",
        "function": {
            "name": "...",
            "description": "...",
            "parameters": { ... JSON Schema ... }
        }
    }
"""


# ── Protocol ──────────────────────────────────────────────────────────────────


@runtime_checkable
class LLMClient(Protocol):
    """Abstract interface for LLM communication."""

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Send a chat completion request to the LLM.

        Args:
            messages: Conversation history as a list of chat messages.
            tools: Optional list of tool schemas the LLM may call.

        Returns:
            Structured LLM response with content and/or tool calls.
        """
        ...


# ── Ollama implementation ─────────────────────────────────────────────────────


class OllamaLLMClient:
    """LLM client wrapping the Ollama async API.

    Uses ``settings.ollama_base_url`` and ``settings.ollama_model``.
    """

    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
    ) -> None:
        self._base_url = base_url or settings.ollama_base_url
        self._model = model or settings.ollama_model

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Send a chat request to the Ollama server."""
        import ollama

        client = ollama.AsyncClient(host=self._base_url)

        # Convert messages to Ollama format (plain dicts).
        ollama_messages = _messages_to_ollama(messages)

        # Convert tool schemas to Ollama format.
        ollama_tools = _tools_to_ollama(tools) if tools else None

        start = time.monotonic()
        try:
            kwargs: dict[str, Any] = {
                "model": self._model,
                "messages": ollama_messages,
            }
            if ollama_tools:
                kwargs["tools"] = ollama_tools

            response = await client.chat(**kwargs)
        finally:
            latency_ms = int((time.monotonic() - start) * 1000)

        # Parse Ollama response.
        message = response.get("message", {})
        content = message.get("content", "") or ""
        raw_tool_calls = message.get("tool_calls") or []

        tool_calls: list[ToolCall] = []
        for i, tc in enumerate(raw_tool_calls):
            func = tc.get("function", {})
            tool_calls.append(
                ToolCall(
                    id=f"call_{i}",
                    name=func.get("name", ""),
                    arguments=func.get("arguments", {}),
                )
            )

        # Token counts from Ollama (if available).
        input_tokens = response.get("prompt_eval_count")
        output_tokens = response.get("eval_count")

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            provider="ollama",
            model=self._model,
        )


# ── Paid API implementation ──────────────────────────────────────────────────


class PaidLLMClient:
    """LLM client wrapping Anthropic or OpenAI SDKs.

    Provider is selected via ``settings.fallback_llm_provider``.
    """

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self._provider = provider or settings.fallback_llm_provider
        self._model = model or settings.fallback_llm_model

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Send a chat request to the paid API."""
        if self._provider == "anthropic":
            return await self._chat_anthropic(messages, tools)
        elif self._provider == "openai":
            return await self._chat_openai(messages, tools)
        else:
            raise ValueError(f"Unknown LLM provider: {self._provider}")

    async def _chat_anthropic(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Send a request to the Anthropic API."""
        import anthropic

        client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)

        # Separate system message from conversation.
        system_text = ""
        api_messages: list[dict[str, Any]] = []
        for msg in messages:
            if msg.role == "system":
                system_text = msg.content
            else:
                api_messages.append({"role": msg.role, "content": msg.content})

        # Convert tools to Anthropic format.
        anthropic_tools = _tools_to_anthropic(tools) if tools else []

        kwargs: dict[str, Any] = {
            "model": self._model,
            "max_tokens": 2048,
            "messages": api_messages,
        }
        if system_text:
            kwargs["system"] = system_text
        if anthropic_tools:
            kwargs["tools"] = anthropic_tools

        start = time.monotonic()
        response = await client.messages.create(**kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)

        # Parse response.
        content = ""
        tool_calls: list[ToolCall] = []

        for block in response.content:
            if block.type == "text":
                content += block.text
            elif block.type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=block.id,
                        name=block.name,
                        arguments=block.input if isinstance(block.input, dict) else {},
                    )
                )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            latency_ms=latency_ms,
            provider="anthropic",
            model=self._model,
        )

    async def _chat_openai(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Send a request to the OpenAI API."""
        import json

        import openai

        client = openai.AsyncOpenAI(api_key=settings.openai_api_key)

        # Convert messages to OpenAI format.
        api_messages: list[dict[str, Any]] = []
        for msg in messages:
            api_messages.append({"role": msg.role, "content": msg.content})

        kwargs: dict[str, Any] = {
            "model": self._model,
            "messages": api_messages,
        }
        if tools:
            kwargs["tools"] = tools  # OpenAI format is the canonical format

        start = time.monotonic()
        response = await client.chat.completions.create(**kwargs)
        latency_ms = int((time.monotonic() - start) * 1000)

        # Parse response.
        choice = response.choices[0]
        content = choice.message.content or ""
        tool_calls: list[ToolCall] = []

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                tool_calls.append(
                    ToolCall(
                        id=tc.id,
                        name=tc.function.name,
                        arguments=json.loads(tc.function.arguments)
                        if isinstance(tc.function.arguments, str)
                        else tc.function.arguments,
                    )
                )

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            input_tokens=response.usage.prompt_tokens if response.usage else None,
            output_tokens=response.usage.completion_tokens if response.usage else None,
            latency_ms=latency_ms,
            provider="openai",
            model=self._model,
        )


# ── Fallback composite client ────────────────────────────────────────────────


class FallbackLLMClient:
    """Composite client: tries local Ollama first, falls back to paid API.

    On Ollama failure (connection error, timeout, malformed response), the
    request is retried via the paid API.  Every call — successful or not — is
    logged to ``llm_calls``.
    """

    def __init__(
        self,
        primary: LLMClient | None = None,
        fallback: LLMClient | None = None,
    ) -> None:
        self._primary = primary or OllamaLLMClient()
        self._fallback = fallback or PaidLLMClient()

    async def chat(
        self,
        messages: list[ChatMessage],
        tools: list[ToolSchema] | None = None,
    ) -> LLMResponse:
        """Try Ollama; on failure fall back to paid API."""
        # Try primary (Ollama).
        try:
            response = await self._primary.chat(messages, tools)
            logger.debug(
                "Ollama responded in %dms (tokens: %s/%s)",
                response.latency_ms or 0,
                response.input_tokens,
                response.output_tokens,
            )
            return response

        except Exception as exc:
            fallback_reason = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "Ollama call failed (%s), falling back to paid API",
                fallback_reason,
            )

        # Fall back to paid API.
        try:
            response = await self._fallback.chat(messages, tools)
            # Tag the response so the caller knows it was a fallback.
            response.provider = f"{response.provider} (fallback)"
            logger.info(
                "Fallback API responded in %dms (reason: %s)",
                response.latency_ms or 0,
                fallback_reason,
            )
            return response

        except Exception as fallback_exc:
            logger.error("Fallback API also failed: %s", fallback_exc)
            raise


# ── Format conversion helpers ─────────────────────────────────────────────────


def _messages_to_ollama(messages: list[ChatMessage]) -> list[dict[str, Any]]:
    """Convert ChatMessage list to Ollama's message format."""
    result: list[dict[str, Any]] = []
    for msg in messages:
        entry: dict[str, Any] = {"role": msg.role, "content": msg.content}
        result.append(entry)
    return result


def _tools_to_ollama(tools: list[ToolSchema] | None) -> list[dict[str, Any]] | None:
    """Convert OpenAI-format tool schemas to Ollama format.

    Ollama uses the same format as OpenAI for function-calling tools.
    """
    if not tools:
        return None
    return tools


def _tools_to_anthropic(tools: list[ToolSchema] | None) -> list[dict[str, Any]]:
    """Convert OpenAI-format tool schemas to Anthropic's tool format.

    OpenAI format::

        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}

    Anthropic format::

        {"name": ..., "description": ..., "input_schema": ...}
    """
    if not tools:
        return []

    result: list[dict[str, Any]] = []
    for tool in tools:
        func = tool.get("function", {})
        result.append(
            {
                "name": func.get("name", ""),
                "description": func.get("description", ""),
                "input_schema": func.get("parameters", {}),
            }
        )
    return result


def _estimate_cost_usd(
    provider: str,
    model: str,
    input_tokens: int | None,
    output_tokens: int | None,
) -> Decimal | None:
    """Rough cost estimate for paid API calls.

    Pricing as of late 2025 — update as needed.
    """
    if provider == "ollama":
        return Decimal("0")

    in_t = input_tokens or 0
    out_t = output_tokens or 0

    # Claude Haiku pricing (per 1M tokens).
    if "haiku" in model.lower():
        return Decimal(str(in_t * 0.25 / 1_000_000 + out_t * 1.25 / 1_000_000))

    # GPT-4o-mini pricing (per 1M tokens).
    if "gpt-4o-mini" in model.lower():
        return Decimal(str(in_t * 0.15 / 1_000_000 + out_t * 0.60 / 1_000_000))

    # Unknown model — return None.
    return None
