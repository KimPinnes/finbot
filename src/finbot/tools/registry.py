"""Lightweight tool registry for LLM function-calling.

Tools are Python async functions with typed schemas that the LLM can invoke.
The registry stores :class:`ToolDef` descriptors and provides methods to:

- Register tools via the :func:`tool` decorator
- Export tool schemas in OpenAI function-calling format (used by all providers)
- Dispatch a tool call by name with argument validation
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Type alias for an async tool handler function.
ToolHandler = Callable[..., Coroutine[Any, Any, Any]]


@dataclass
class ToolDef:
    """Definition of a single tool callable by the LLM.

    Attributes:
        name: Unique tool name (used in LLM function-calling).
        description: Human-readable description shown to the LLM.
        parameters_schema: JSON Schema dict describing the tool's parameters.
        handler: Async function that executes the tool logic.
    """

    name: str
    description: str
    parameters_schema: dict[str, Any]
    handler: ToolHandler


class ToolRegistry:
    """Registry of tools available to the LLM agent.

    Usage::

        registry = ToolRegistry()

        @registry.tool(
            name="get_balance",
            description="Get the current balance between partners.",
            parameters_schema={
                "type": "object",
                "properties": {},
                "required": [],
            },
        )
        async def get_balance() -> dict:
            ...

        # Export schemas for the LLM.
        schemas = registry.get_tools_for_llm()

        # Execute a tool call from the LLM.
        result = await registry.execute_tool("get_balance", {})
    """

    def __init__(self) -> None:
        self._tools: dict[str, ToolDef] = {}

    def tool(
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
    ) -> Callable[[ToolHandler], ToolHandler]:
        """Decorator to register a tool function.

        Args:
            name: Unique tool name.
            description: What the tool does (shown to LLM).
            parameters_schema: JSON Schema for the tool's parameters.

        Returns:
            The original function, unmodified.
        """

        def decorator(func: ToolHandler) -> ToolHandler:
            if name in self._tools:
                raise ValueError(f"Tool '{name}' is already registered")
            self._tools[name] = ToolDef(
                name=name,
                description=description,
                parameters_schema=parameters_schema,
                handler=func,
            )
            logger.debug("Registered tool: %s", name)
            return func

        return decorator

    def register(
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        handler: ToolHandler,
    ) -> None:
        """Imperatively register a tool (alternative to the decorator).

        Args:
            name: Unique tool name.
            description: What the tool does (shown to LLM).
            parameters_schema: JSON Schema for the tool's parameters.
            handler: Async function that executes the tool logic.
        """
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered")
        self._tools[name] = ToolDef(
            name=name,
            description=description,
            parameters_schema=parameters_schema,
            handler=handler,
        )
        logger.debug("Registered tool: %s", name)

    def get_tool(self, name: str) -> ToolDef | None:
        """Look up a tool by name.

        Returns:
            The :class:`ToolDef` if found, else ``None``.
        """
        return self._tools.get(name)

    def list_tools(self) -> list[ToolDef]:
        """Return all registered tools."""
        return list(self._tools.values())

    def get_tools_for_llm(self) -> list[dict[str, Any]]:
        """Export tool schemas in OpenAI function-calling format.

        Returns a list of dicts, each with::

            {
                "type": "function",
                "function": {
                    "name": "...",
                    "description": "...",
                    "parameters": { ... JSON Schema ... }
                }
            }

        This format is used by OpenAI, Ollama, and (after conversion)
        Anthropic.
        """
        schemas: list[dict[str, Any]] = []
        for tool_def in self._tools.values():
            schemas.append(
                {
                    "type": "function",
                    "function": {
                        "name": tool_def.name,
                        "description": tool_def.description,
                        "parameters": tool_def.parameters_schema,
                    },
                }
            )
        return schemas

    async def execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
    ) -> Any:
        """Execute a registered tool by name.

        Args:
            name: The tool name (must be registered).
            arguments: Keyword arguments to pass to the tool handler.

        Returns:
            The tool handler's return value.

        Raises:
            KeyError: If the tool is not registered.
        """
        tool_def = self._tools.get(name)
        if tool_def is None:
            raise KeyError(f"Unknown tool: '{name}'")

        logger.info("Executing tool: %s(%s)", name, arguments)
        return await tool_def.handler(**arguments)


# ── Global registry instance ──────────────────────────────────────────────────
# Tools register themselves on import via the decorator.

default_registry = ToolRegistry()
