# FinBot

Natural-language shared finance agent via Telegram. Two partners send free-text messages (expenses, settlements, queries); an LLM agent parses them, confirms, and commits to an append-only ledger.

## Features

- **Natural language input** — send messages like *"groceries 300, I paid, split 50/50"*
- **Multi-step confirmation** — the bot clarifies ambiguities before committing
- **Append-only ledger** — immutable audit trail with derived balances
- **Local LLM primary** — runs on Ollama (Qwen2.5-7B) with paid API fallback
- **Settlements** — record direct payments between partners
- **Expense queries** — ask about balances, spending by category, recent activity
- **Category management** — view and rename categories via `/categories`
- **Observability** — every LLM call logged with latency, tokens, and cost

## Prerequisites

- **Python 3.11+**
- **PostgreSQL 16** (or use the Docker Compose DB)
- **Telegram Bot Token** from [@BotFather](https://t.me/BotFather)
- **Ollama** (optional, for local LLM) or a paid API key (Anthropic/OpenAI) for the agent

## Quick Start

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
- `DATABASE_URL` — if not using Docker: `postgresql+asyncpg://user:pass@localhost:5433/finbot`
- For the agent: either run **Ollama** and set `OLLAMA_BASE_URL` / `OLLAMA_MODEL`, or set `FALLBACK_LLM_PROVIDER`, `FALLBACK_LLM_MODEL`, and the corresponding API key (`ANTHROPIC_API_KEY` or `OPENAI_API_KEY`)

See `.env.example` for all options (currency, default split behavior, debug, etc.).

### 4. Database

**Option A — Docker (recommended for dev)**

```bash
docker compose up -d db
```

This starts PostgreSQL on port 5433 with `DATABASE_URL=postgresql+asyncpg://finbot:finbot@localhost:5433/finbot`.

**Option B — Existing PostgreSQL**

Create a database and user, then set `DATABASE_URL` in `.env`.

**Run migrations**

```bash
alembic upgrade head
```

### 5. Start the bot

```bash
python -m finbot
```

## Docker Deployment

### Development (app + DB)

```bash
docker compose up -d
```

The `finbot` service runs the bot (see `docker-compose.yml`). It uses `entrypoint.sh` to run migrations then start the bot. Source is volume-mounted for hot-reload.

### Production (app + DB + Ollama with GPU)

```bash
docker compose -f docker-compose.prod.yml up -d
```

Uses `docker-compose.prod.yml` with Ollama GPU passthrough (requires NVIDIA Container Toolkit on the host).

## Usage

1. **Open Telegram** and find your bot; only users whose IDs are in `ALLOWED_TELEGRAM_USER_IDS` can use it.
2. **Send natural-language messages**, e.g.:
   - *"Coffee 25 shekels"* — log an expense
   - *"I paid 100 for groceries, split half"* — expense with split
   - *"She paid me back 50"* — settlement
   - *"Settled in full"* or *"all"* — settle the full balance (bot will use current balance)
   - *"What did we spend on food this month?"* — query

The agent will parse, ask for confirmation when needed, then commit to the ledger. Use inline keyboards to confirm or cancel.

### Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Welcome message and brief instructions |
| `/help` | Detailed usage guide |
| `/balance` | Show the current balance between partners |
| `/setup <partner_id>` | Create a partnership (one-time setup) |
| `/add` | Open the add-expense web app (category picker → amount, split, date) |
| `/categories` | View and rename expense categories (type *cancel* to abort a rename) |

The **add-expense web app** (`/add`) runs only inside Telegram. If opened in a regular browser you’ll see an “Access denied” message; use the bot and tap `/add` to use it.

- **User-facing actions:** See [docs/USER_ACTIONS.md](docs/USER_ACTIONS.md) for the full list of commands and actions (kept in sync with the Telegram menu and `/help`).
- **Planned features:** See [docs/FUTURE_FEATURES.md](docs/FUTURE_FEATURES.md) for the backlog of future features and ideas.

## Project Structure

```
finbot/
├── src/finbot/           # Application source
│   ├── agent/            # LLM orchestration (state machine, prompts, client)
│   ├── bot/              # Telegram handlers, keyboards, formatters, middleware
│   ├── db/               # Async DB session & Alembic migrations
│   ├── ledger/           # ORM models, repository, balance derivation, validation
│   ├── reprocessing/     # Historical re-parsing (future)
│   └── tools/            # Tool registry & implementations (expenses, queries, etc.)
├── tests/                # Mirrors src/ structure
├── docs/                 # Design, ADRs, user actions, future features
├── docker-compose.yml    # Dev compose (macOS)
├── docker-compose.prod.yml  # Prod compose (Ubuntu + GPU)
└── pyproject.toml        # Dependencies & tool config
```

See `docs/design.md` for the full technical design and `docs/decisions.md` for architecture decision records.

## Development

- **Tests:** `pytest`
- **Lint:** `ruff check src tests`
- **Format:** `ruff format src tests`

## License

MIT
