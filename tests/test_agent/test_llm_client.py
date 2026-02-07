"""Tests for the LLM client abstraction layer.

Tests cover:
- OllamaLLMClient with mocked Ollama async client
- PaidLLMClient with mocked Anthropic and OpenAI SDKs
- FallbackLLMClient composite behavior (primary → fallback)
- Response parsing and tool call extraction
- Format conversion helpers
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from finbot.agent.llm_client import (
    ChatMessage,
    FallbackLLMClient,
    LLMResponse,
    OllamaLLMClient,
    PaidLLMClient,
    ToolCall,
    _estimate_cost_usd,
    _messages_to_ollama,
    _tools_to_anthropic,
)

# ── ChatMessage / LLMResponse model tests ─────────────────────────────────────


def test_chat_message_creation() -> None:
    msg = ChatMessage(role="user", content="hello")
    assert msg.role == "user"
    assert msg.content == "hello"


def test_llm_response_defaults() -> None:
    resp = LLMResponse()
    assert resp.content == ""
    assert resp.tool_calls == []
    assert resp.input_tokens is None
    assert resp.provider == ""


def test_tool_call_model() -> None:
    tc = ToolCall(id="call_0", name="parse_expense", arguments={"text": "coffee 25"})
    assert tc.name == "parse_expense"
    assert tc.arguments == {"text": "coffee 25"}


# ── Format conversion helpers ─────────────────────────────────────────────────


def test_messages_to_ollama() -> None:
    messages = [
        ChatMessage(role="system", content="You are helpful."),
        ChatMessage(role="user", content="Hello"),
    ]
    result = _messages_to_ollama(messages)
    assert len(result) == 2
    assert result[0] == {"role": "system", "content": "You are helpful."}
    assert result[1] == {"role": "user", "content": "Hello"}


def test_tools_to_anthropic() -> None:
    tools = [
        {
            "type": "function",
            "function": {
                "name": "get_balance",
                "description": "Get balance",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]
    result = _tools_to_anthropic(tools)
    assert len(result) == 1
    assert result[0]["name"] == "get_balance"
    assert result[0]["description"] == "Get balance"
    assert result[0]["input_schema"] == {"type": "object", "properties": {}}


def test_tools_to_anthropic_empty() -> None:
    assert _tools_to_anthropic(None) == []
    assert _tools_to_anthropic([]) == []


# ── Cost estimation ───────────────────────────────────────────────────────────


def test_estimate_cost_ollama_is_zero() -> None:
    from decimal import Decimal

    cost = _estimate_cost_usd("ollama", "qwen2.5", 100, 50)
    assert cost == Decimal("0")


def test_estimate_cost_haiku() -> None:
    cost = _estimate_cost_usd("anthropic", "claude-3-5-haiku-latest", 1000, 500)
    assert cost is not None
    assert cost > 0


def test_estimate_cost_gpt4o_mini() -> None:
    cost = _estimate_cost_usd("openai", "gpt-4o-mini", 1000, 500)
    assert cost is not None
    assert cost > 0


def test_estimate_cost_unknown_model() -> None:
    cost = _estimate_cost_usd("other", "unknown-model", 1000, 500)
    assert cost is None


# ── OllamaLLMClient tests ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_ollama_client_basic_response() -> None:
    """OllamaLLMClient should parse a basic text response from Ollama."""
    mock_response = {
        "message": {
            "content": "Hello! How can I help?",
            "tool_calls": None,
        },
        "prompt_eval_count": 50,
        "eval_count": 20,
    }

    with patch("ollama.AsyncClient") as mock_ollama_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value=mock_response)
        mock_ollama_cls.return_value = instance

        client = OllamaLLMClient(base_url="http://test:11434", model="test-model")
        messages = [ChatMessage(role="user", content="hi")]

        result = await client.chat(messages)

    assert result.content == "Hello! How can I help?"
    assert result.tool_calls == []
    assert result.input_tokens == 50
    assert result.output_tokens == 20
    assert result.provider == "ollama"
    assert result.model == "test-model"
    assert result.latency_ms is not None


@pytest.mark.asyncio
async def test_ollama_client_with_tool_calls() -> None:
    """OllamaLLMClient should parse tool calls from the response."""
    mock_response = {
        "message": {
            "content": "",
            "tool_calls": [
                {
                    "function": {
                        "name": "parse_expense",
                        "arguments": {"text": "groceries 300"},
                    }
                }
            ],
        },
        "prompt_eval_count": 100,
        "eval_count": 30,
    }

    with patch("ollama.AsyncClient") as mock_ollama_cls:
        instance = AsyncMock()
        instance.chat = AsyncMock(return_value=mock_response)
        mock_ollama_cls.return_value = instance

        client = OllamaLLMClient(base_url="http://test:11434", model="test-model")
        messages = [ChatMessage(role="user", content="groceries 300")]

        result = await client.chat(messages)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "parse_expense"
    assert result.tool_calls[0].arguments == {"text": "groceries 300"}


# ── PaidLLMClient tests ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_paid_client_anthropic_basic() -> None:
    """PaidLLMClient should handle Anthropic text responses."""
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = "Parsed your expense."

    mock_response = MagicMock()
    mock_response.content = [mock_block]
    mock_response.usage.input_tokens = 80
    mock_response.usage.output_tokens = 15

    with patch("anthropic.AsyncAnthropic") as mock_anthropic_cls:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic_cls.return_value = mock_client

        client = PaidLLMClient(provider="anthropic", model="claude-3-5-haiku-latest")
        messages = [
            ChatMessage(role="system", content="You are helpful."),
            ChatMessage(role="user", content="groceries 300"),
        ]

        result = await client.chat(messages)

    assert result.content == "Parsed your expense."
    assert result.input_tokens == 80
    assert result.output_tokens == 15
    assert result.provider == "anthropic"


@pytest.mark.asyncio
async def test_paid_client_anthropic_tool_use() -> None:
    """PaidLLMClient should parse Anthropic tool_use blocks."""
    mock_text_block = MagicMock()
    mock_text_block.type = "text"
    mock_text_block.text = ""

    mock_tool_block = MagicMock()
    mock_tool_block.type = "tool_use"
    mock_tool_block.id = "toolu_123"
    mock_tool_block.name = "parse_expense"
    mock_tool_block.input = {"expenses": [{"amount": 300}], "intent": "expense"}

    mock_response = MagicMock()
    mock_response.content = [mock_text_block, mock_tool_block]
    mock_response.usage.input_tokens = 100
    mock_response.usage.output_tokens = 40

    with patch("anthropic.AsyncAnthropic") as mock_anthropic_cls:
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic_cls.return_value = mock_client

        client = PaidLLMClient(provider="anthropic", model="claude-3-5-haiku-latest")
        messages = [ChatMessage(role="user", content="groceries 300")]

        result = await client.chat(messages)

    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].id == "toolu_123"
    assert result.tool_calls[0].name == "parse_expense"
    assert result.tool_calls[0].arguments["expenses"] == [{"amount": 300}]


@pytest.mark.asyncio
async def test_paid_client_openai_basic() -> None:
    """PaidLLMClient should handle OpenAI text responses."""
    mock_choice = MagicMock()
    mock_choice.message.content = "Here's your expense."
    mock_choice.message.tool_calls = None

    mock_response = MagicMock()
    mock_response.choices = [mock_choice]
    mock_response.usage.prompt_tokens = 60
    mock_response.usage.completion_tokens = 10

    with patch("openai.AsyncOpenAI") as mock_openai_cls:
        mock_client = AsyncMock()
        mock_client.chat.completions.create = AsyncMock(return_value=mock_response)
        mock_openai_cls.return_value = mock_client

        client = PaidLLMClient(provider="openai", model="gpt-4o-mini")
        messages = [ChatMessage(role="user", content="hello")]

        result = await client.chat(messages)

    assert result.content == "Here's your expense."
    assert result.input_tokens == 60
    assert result.output_tokens == 10
    assert result.provider == "openai"


@pytest.mark.asyncio
async def test_paid_client_unknown_provider_raises() -> None:
    """PaidLLMClient should raise ValueError for unknown providers."""
    client = PaidLLMClient(provider="unknown", model="test")
    messages = [ChatMessage(role="user", content="hi")]

    with pytest.raises(ValueError, match="Unknown LLM provider"):
        await client.chat(messages)


# ── FallbackLLMClient tests ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fallback_uses_primary_on_success() -> None:
    """FallbackLLMClient should use primary when it succeeds."""
    primary = AsyncMock()
    primary.chat = AsyncMock(
        return_value=LLMResponse(content="primary ok", provider="ollama", model="test")
    )
    fallback = AsyncMock()
    fallback.chat = AsyncMock()

    client = FallbackLLMClient(primary=primary, fallback=fallback)
    messages = [ChatMessage(role="user", content="hello")]

    result = await client.chat(messages)

    assert result.content == "primary ok"
    primary.chat.assert_called_once()
    fallback.chat.assert_not_called()


@pytest.mark.asyncio
async def test_fallback_uses_fallback_on_primary_failure() -> None:
    """FallbackLLMClient should fall back when primary raises."""
    primary = AsyncMock()
    primary.chat = AsyncMock(side_effect=ConnectionError("Ollama down"))

    fallback = AsyncMock()
    fallback.chat = AsyncMock(
        return_value=LLMResponse(content="fallback ok", provider="anthropic", model="haiku")
    )

    client = FallbackLLMClient(primary=primary, fallback=fallback)
    messages = [ChatMessage(role="user", content="hello")]

    result = await client.chat(messages)

    assert result.content == "fallback ok"
    assert "fallback" in result.provider
    primary.chat.assert_called_once()
    fallback.chat.assert_called_once()


@pytest.mark.asyncio
async def test_fallback_raises_when_both_fail() -> None:
    """FallbackLLMClient should raise when both primary and fallback fail."""
    primary = AsyncMock()
    primary.chat = AsyncMock(side_effect=ConnectionError("Ollama down"))

    fallback = AsyncMock()
    fallback.chat = AsyncMock(side_effect=RuntimeError("API error"))

    client = FallbackLLMClient(primary=primary, fallback=fallback)
    messages = [ChatMessage(role="user", content="hello")]

    with pytest.raises(RuntimeError, match="API error"):
        await client.chat(messages)
