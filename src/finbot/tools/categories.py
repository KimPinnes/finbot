"""Category management tools for the LLM agent.

Provides tools for managing expense categories:

- ``list_categories`` — return known expense categories from the database
- ``create_category`` — add a new user-defined category
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from finbot.ledger.models import Category
from finbot.ledger.repository import save_category as _save_category
from finbot.tools.registry import default_registry


@default_registry.tool(
    name="list_categories",
    description=(
        "List all known expense categories. Use this to validate or suggest "
        "a category when parsing an expense. Returns a list of category names."
    ),
    parameters_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
)
async def list_categories(*, session: AsyncSession | None = None) -> dict:
    """Return all expense categories from the database.

    Args:
        session: Async database session.  If ``None`` (e.g. during tests or
            when the DB is unavailable), returns a set of default categories.

    Returns:
        A dict with a ``categories`` key containing a list of category name
        strings.
    """
    if session is None:
        # Return sensible defaults when no DB session is available.
        return {
            "categories": _default_categories(),
        }

    result = await session.execute(
        select(Category.name).order_by(Category.name)
    )
    names = list(result.scalars().all())

    # If the categories table is empty, return defaults.
    if not names:
        return {
            "categories": _default_categories(),
        }

    return {"categories": names}


@default_registry.tool(
    name="create_category",
    description=(
        "Add a new user-defined expense category. The category name is "
        "normalised to lowercase. Returns the created category or indicates "
        "that it already exists."
    ),
    parameters_schema={
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "The category name to create (e.g. 'dining', 'petcare').",
            },
        },
        "required": ["name"],
    },
)
async def create_category(
    *,
    name: str,
    session: AsyncSession | None = None,
) -> dict:
    """Add a new expense category to the database.

    The category name is normalised to lowercase before insertion.
    If the category already exists, a message is returned instead of
    creating a duplicate.

    Args:
        name: The category name to create.
        session: Async database session.  If ``None``, returns an error.

    Returns:
        A dict with ``success``, ``name``, and ``message`` keys.
    """
    if session is None:
        return {"error": "Category creation requires a database session."}

    normalised = name.strip().lower()
    if not normalised:
        return {"error": "Category name cannot be empty."}

    category, created = await _save_category(session, normalised)

    if created:
        return {
            "success": True,
            "name": category.name,
            "message": f"Category '{category.name}' created.",
        }

    return {
        "success": True,
        "name": category.name,
        "message": f"Category '{category.name}' already exists.",
    }


def _default_categories() -> list[str]:
    """Return a list of common default expense categories."""
    return [
        "clothing",
        "coffee",
        "dining",
        "education",
        "entertainment",
        "gas",
        "gifts",
        "groceries",
        "health",
        "home",
        "insurance",
        "personal",
        "subscriptions",
        "transport",
        "travel",
        "utilities",
    ]
