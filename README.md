# FinBot

Natural-language shared finance agent via Telegram. Two partners send free-text messages (expenses, settlements, queries); an LLM agent parses them, confirms, and commits to an append-only ledger.

## Prerequisites

- **Python 3.11+**
- **PostgreSQL 16** (or use the Docker Compose DB)
- **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)
- **Ollama** (optional, for local LLM) or a paid API key (Anthropic/OpenAI) for the agent

## Initialization

### 1. Clone and enter the repo

```bash
git clone <repo-url>
cd finbot
```

### 2. Create a virtual environment and install dependencies

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

### 3. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set at least:

- `TELEGRAM_BOT_TOKEN` — from BotFather
- `ALLOWED_TELEGRAM_USER_IDS` — comma-separated Telegram user IDs (e.g. `[123456789,987654321]`)
- `DATABASE_URL` — if not using Docker: `postgresql+asyncpg://user:pass@localhost:5432/finbot`
- For the agent: either run **Ollama** and set `OLLAMA_BASE_URL` / `OLLAMA_MODEL`, or set `FALLBACK_LLM_PROVIDER`, `FALLBACK_LLM_MODEL`, and the corresponding API key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`)

See `.env.example` for all options (currency, default split behavior, debug, etc.).

### 4. Database

**Option A — Docker (recommended for dev)**

```bash
docker compose up -d
```

This starts PostgreSQL; the app can run on the host (see Startup below) with `DATABASE_URL=postgresql+asyncpg://finbot:finbot@localhost:5432/finbot`.

**Option B — Existing PostgreSQL**

Create a database and user, then set `DATABASE_URL` in `.env`.

**Run migrations**

```bash
alembic upgrade head
```

## Startup

### With Docker Compose (app + DB)

```bash
docker compose up -d
```

The `finbot` service runs the bot (see `docker-compose.yml`). It uses `entrypoint.sh`: migrations then `python -m finbot`.

### Local (app only, DB in Docker or external)

```bash
# If using Docker for DB only:
docker compose up -d db

# Then run the bot on your machine:
source .venv/bin/activate
alembic upgrade head
python -m finbot
```

### Production

Use `docker-compose.prod.yml` for production-style deployment (see that file for overrides).

## Operation

1. **Start the bot** (Docker or `python -m finbot` as above).
2. **Open Telegram** and find your bot; only users whose IDs are in `ALLOWED_TELEGRAM_USER_IDS` can use it.
3. **Send natural-language messages**, e.g.:
   - *"Coffee 25 shekels"* — log an expense
   - *"I paid 100 for groceries, split half"* — expense with split
   - *"She paid me back 50"* — settlement
   - *"What did we spend on food this month?"* — query
4. The agent will parse, ask for confirmation when needed, then commit to the ledger. Use inline keyboards to confirm or cancel.

## Development

- **Tests:** `pytest`
- **Lint:** `ruff check src tests`
- **Format:** `ruff format src tests`

Design and decisions are in `docs/design.md` and `docs/decisions.md`.
