"""Tool implementations for the LLM agent.

Importing this package ensures all tools are registered with the
:data:`~finbot.tools.registry.default_registry`.
"""

# Import tool modules so their @default_registry.tool decorators execute.
from finbot.tools import categories, expenses, queries, settlements  # noqa: F401
from finbot.tools.registry import default_registry

__all__ = ["default_registry"]
