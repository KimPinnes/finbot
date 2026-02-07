"""Tests for the tool registry.

Tests cover:
- Tool registration via decorator and imperative API
- Tool lookup and listing
- Schema export in OpenAI function-calling format
- Tool execution dispatch
- Error handling (duplicate registration, unknown tool)
"""

from __future__ import annotations

import pytest

from finbot.tools.registry import ToolRegistry

# ── Registration tests ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_register_tool_via_decorator() -> None:
    """Tools registered via decorator should be retrievable."""
    registry = ToolRegistry()

    @registry.tool(
        name="test_tool",
        description="A test tool.",
        parameters_schema={"type": "object", "properties": {}, "required": []},
    )
    async def my_tool() -> str:
        return "result"

    tool = registry.get_tool("test_tool")
    assert tool is not None
    assert tool.name == "test_tool"
    assert tool.description == "A test tool."
    assert tool.handler is my_tool


def test_register_tool_imperatively() -> None:
    """Tools registered via register() should be retrievable."""
    registry = ToolRegistry()

    async def handler(x: int) -> int:
        return x * 2

    registry.register(
        name="double",
        description="Double a number.",
        parameters_schema={
            "type": "object",
            "properties": {"x": {"type": "integer"}},
            "required": ["x"],
        },
        handler=handler,
    )

    tool = registry.get_tool("double")
    assert tool is not None
    assert tool.name == "double"


def test_duplicate_registration_raises() -> None:
    """Registering the same tool name twice should raise ValueError."""
    registry = ToolRegistry()

    async def handler() -> None:
        pass

    registry.register(
        name="dup",
        description="First.",
        parameters_schema={"type": "object", "properties": {}},
        handler=handler,
    )

    with pytest.raises(ValueError, match="already registered"):
        registry.register(
            name="dup",
            description="Second.",
            parameters_schema={"type": "object", "properties": {}},
            handler=handler,
        )


def test_duplicate_decorator_registration_raises() -> None:
    """Decorating two functions with the same tool name should raise."""
    registry = ToolRegistry()

    @registry.tool(
        name="same_name",
        description="First.",
        parameters_schema={"type": "object", "properties": {}},
    )
    async def first() -> None:
        pass

    with pytest.raises(ValueError, match="already registered"):

        @registry.tool(
            name="same_name",
            description="Second.",
            parameters_schema={"type": "object", "properties": {}},
        )
        async def second() -> None:
            pass


# ── Lookup and listing ────────────────────────────────────────────────────────


def test_get_tool_returns_none_for_unknown() -> None:
    registry = ToolRegistry()
    assert registry.get_tool("nonexistent") is None


def test_list_tools_returns_all() -> None:
    registry = ToolRegistry()

    async def handler() -> None:
        pass

    registry.register(
        name="a", description="A", parameters_schema={}, handler=handler
    )
    registry.register(
        name="b", description="B", parameters_schema={}, handler=handler
    )

    tools = registry.list_tools()
    assert len(tools) == 2
    names = {t.name for t in tools}
    assert names == {"a", "b"}


# ── Schema export ─────────────────────────────────────────────────────────────


def test_get_tools_for_llm_format() -> None:
    """Exported schemas should match the OpenAI function-calling format."""
    registry = ToolRegistry()

    async def handler(text: str) -> dict:
        return {}

    registry.register(
        name="parse",
        description="Parse text.",
        parameters_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
        handler=handler,
    )

    schemas = registry.get_tools_for_llm()
    assert len(schemas) == 1

    schema = schemas[0]
    assert schema["type"] == "function"
    assert schema["function"]["name"] == "parse"
    assert schema["function"]["description"] == "Parse text."
    assert schema["function"]["parameters"]["type"] == "object"
    assert "text" in schema["function"]["parameters"]["properties"]


def test_get_tools_for_llm_empty_registry() -> None:
    registry = ToolRegistry()
    assert registry.get_tools_for_llm() == []


# ── Tool execution ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_tool_calls_handler() -> None:
    """execute_tool should invoke the handler with the provided arguments."""
    registry = ToolRegistry()

    async def adder(a: int, b: int) -> int:
        return a + b

    registry.register(
        name="add",
        description="Add two numbers.",
        parameters_schema={
            "type": "object",
            "properties": {
                "a": {"type": "integer"},
                "b": {"type": "integer"},
            },
            "required": ["a", "b"],
        },
        handler=adder,
    )

    result = await registry.execute_tool("add", {"a": 3, "b": 7})
    assert result == 10


@pytest.mark.asyncio
async def test_execute_unknown_tool_raises() -> None:
    """execute_tool should raise KeyError for unregistered tools."""
    registry = ToolRegistry()

    with pytest.raises(KeyError, match="Unknown tool"):
        await registry.execute_tool("nonexistent", {})
