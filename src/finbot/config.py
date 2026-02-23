"""Application settings loaded from environment variables / .env file."""

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_CATEGORIES = [
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

# Subtypes of "utilities" (internet, electricity, etc.) — map to category "utilities".
UTILITY_SUBTYPES: frozenset[str] = frozenset({
    "electricity", "electric", "water", "internet", "phone", "heating",
    "trash", "sewage", "broadband",
})


def _strip_str(v: str | object) -> str | object:
    """Strip whitespace from string env values (common .env copy-paste issue)."""
    return v.strip() if isinstance(v, str) else v


class Settings(BaseSettings):
    """FinBot configuration.

    Values are loaded from environment variables and/or an ``.env`` file
    located at the project root.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # ── Database ──────────────────────────────────────────────────────
    database_url: str = Field(
        default="postgresql+asyncpg://finbot:finbot@localhost:5433/finbot",
        description="Async PostgreSQL connection URI.",
    )

    # ── Telegram ──────────────────────────────────────────────────────
    telegram_bot_token: str = Field(
        default="",
        description="Bot token from @BotFather.",
    )
    allowed_telegram_user_ids: list[int] = Field(
        default_factory=list,
        description="Telegram user IDs allowed to interact with the bot.",
    )

    # ── Local LLM (Ollama) ────────────────────────────────────────────
    ollama_base_url: str = Field(
        default="http://localhost:11434",
        description="Ollama API base URL.",
    )
    ollama_model: str = Field(
        default="qwen2.5:7b-instruct-q4_K_M",
        description="Ollama model name to use for inference.",
    )

    # ── Fallback LLM (paid API) ──────────────────────────────────────
    fallback_llm_provider: str = Field(
        default="anthropic",
        description="Fallback provider: 'anthropic' or 'openai'.",
    )
    fallback_llm_model: str = Field(
        default="claude-3-5-haiku-latest",
        description="Model name for the fallback provider.",
    )
    anthropic_api_key: str = Field(
        default="",
        description="Anthropic API key (required if fallback_llm_provider='anthropic').",
    )
    openai_api_key: str = Field(
        default="",
        description="OpenAI API key (required if fallback_llm_provider='openai').",
    )

    @field_validator("anthropic_api_key", "openai_api_key", mode="before")
    @classmethod
    def strip_api_keys(cls, v: str | object) -> str | object:
        return _strip_str(v)

    # ── Categories ────────────────────────────────────────────────────
    default_categories_str: str = Field(
        default="",
        alias="DEFAULT_CATEGORIES",
        description=(
            "Comma-separated default expense categories seeded into the DB "
            "on first boot (e.g. 'groceries,dining,utilities')."
        ),
    )

    @property
    def default_categories(self) -> list[str]:
        """Parsed list of default categories from the env string."""
        if self.default_categories_str.strip():
            return [c.strip().lower() for c in self.default_categories_str.split(",") if c.strip()]
        return list(_DEFAULT_CATEGORIES)

    # ── Mini App ─────────────────────────────────────────────────────
    webapp_base_url: str = Field(
        default="",
        description=(
            "Base URL of the Telegram Mini App (e.g. "
            "'https://user.github.io/finbot/webapp/'). "
            "When empty, the /add command is disabled."
        ),
    )
    webapp_api_url: str = Field(
        default="",
        description=(
            "Public HTTPS URL of the Mini App API server. "
            "Passed to the Mini App as the &api= query parameter. "
            "When empty, the Mini App uses its own origin (same-origin serving)."
        ),
    )
    webapp_port: int = Field(
        default=9876,
        description="Port for the Mini App API server.",
    )

    # ── Tunnel ────────────────────────────────────────────────────────
    tunnel_enabled: bool = Field(
        default=False,
        description=(
            "Start a localtunnel on boot to expose the API server publicly. "
            "Requires `lt` CLI (npm install -g localtunnel) or `npx`."
        ),
    )
    tunnel_subdomain: str = Field(
        default="",
        description="Request a specific localtunnel subdomain for a stable URL.",
    )

    # ── General ───────────────────────────────────────────────────────
    default_currency: str = Field(
        default="ILS",
        description="Default currency code for expenses.",
    )
    assume_half_split: bool = Field(
        default=False,
        description="Assume a 50/50 split when the user doesn't specify one.",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug logging.",
    )


settings = Settings()
