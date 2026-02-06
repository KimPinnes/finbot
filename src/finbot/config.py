"""Application settings loaded from environment variables / .env file."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        default="postgresql+asyncpg://finbot:finbot@localhost:5432/finbot",
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

    # ── General ───────────────────────────────────────────────────────
    default_currency: str = Field(
        default="ILS",
        description="Default currency code for expenses.",
    )
    debug: bool = Field(
        default=False,
        description="Enable debug logging.",
    )


settings = Settings()
