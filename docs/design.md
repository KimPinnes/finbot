# FinBot — Technical Design

> Natural Language Shared Finance Agent via Telegram

---

## 1. System Overview

FinBot is a Telegram-based shared finance manager for two partners. Users send free-text messages describing expenses, settlements, and queries. An LLM agent parses these into structured financial data, confirms with the user, and commits to an append-only ledger.

### 1.1 Design Goals

- **Cross-platform**: Android, iOS, Desktop via Telegram
- **Low-cost**: Runs primarily on local LLM; paid API as fallback only
- **Auditable**: Immutable raw inputs, append-only ledger, derived balances
- **Modular**: Clean separation of concerns for iterative development
- **Self-hosted**: Runs on user's Ubuntu server via Docker

---

## 2. Architecture

```
┌────────────────────────┐
│   Telegram Bot API      │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Bot Service            │  Python (aiogram 3.x)
│  - Message routing      │  - Handles Telegram updates
│  - User session mgmt    │  - Inline keyboards for confirmations
│  - Rate limiting        │
└──────────┬─────────────┘
           │
           ▼
┌────────────────────────┐
│  Agent Orchestrator     │  Multi-step state machine
│  - Conversation state   │  - Decides: parse / clarify / commit / query
│  - Agent loop           │  - Calls LLM + tools in sequence
│  - Confidence gating    │  - Manages clarification flow
└──────┬─────────┬───────┘
       │         │
       ▼         ▼
┌────────────┐ ┌──────────────────┐
│ LLM Client │ │  Tool Registry    │
│ - Ollama   │ │  - parse_expense  │
│   (primary)│ │  - log_expense    │
│ - Paid API │ │  - log_settlement │
│   (fallbk) │ │  - get_balance    │
└────────────┘ │  - query_expenses │
               │  - list_categories│
               │  - validate_settl │
               └────────┬─────────┘
                        │
                        ▼
               ┌──────────────────┐
               │  PostgreSQL 16    │
               │  - raw_inputs     │
               │  - ledger         │
               │  - categories     │
               │  - conversations  │
               └──────────────────┘
```

---

## 3. LLM Strategy

### 3.1 Primary: Local Model via Ollama

- **Model**: Qwen2.5-7B-Instruct (Q4_K_M quantization)
- **VRAM**: ~5.5GB on 8GB RTX 5060 Ti (leaves headroom for KV cache)
- **Performance**: Expected 30-60 tokens/sec
- **Tool calling**: Native support in Qwen2.5
- **Context management**: Keep context short — only current interaction + system prompt. History lives in DB, not LLM context.

### 3.2 Fallback: Paid API

- **Models**: Claude Haiku or GPT-4o-mini
- **Trigger**: When local model confidence is low or tool calling fails
- **Budget**: < $5/month target (expected < $1/month at normal usage)
- **Logging**: Every fallback call is logged with reason, input, and cost. A reporting mechanism allows tracking fallback frequency to ensure it stays within budget and to identify patterns that could be improved locally.

### 3.3 Why Not a Thinking Model

The multi-step reasoning is implemented in **application code** (the agent orchestrator), not in the LLM's chain-of-thought. Each individual LLM call is a focused task (parse this text, generate this clarification). This is more reliable and debuggable than depending on an LLM's internal reasoning, especially at 7B parameter scale.

---

## 4. Agent Orchestrator

### 4.1 Multi-Step Flow

The orchestrator is a **custom state machine** (not LangGraph — see [ADR-002](decisions.md#adr-002-custom-agent-loop-over-langgraph)).

States:

```
IDLE → PARSING → VALIDATING → CLARIFYING → CONFIRMING → COMMITTING → IDLE
                                   ↑              │
                                   └──────────────┘  (user edits)
```

### 4.2 Flow Example: Expense Logging

```
User: "groceries 300 and gas 200, yesterday, split 70/30"

Step 1 — PARSING
  LLM extracts structured data:
    Entry 1: {amount: 300, category: "groceries", date: yesterday, split: 70/30}
    Entry 2: {amount: 200, category: "gas", date: yesterday, split: 70/30}

Step 2 — VALIDATING
  Code checks required fields:
    ✓ amount, ✓ category, ✓ date, ✓ split
    ✗ payer — MISSING

Step 3 — CLARIFYING
  Agent asks: "Who paid for these — you or partner?"
  User replies: "me"

Step 4 — VALIDATING (re-run)
  All fields resolved ✓

Step 5 — CONFIRMING
  Bot shows structured summary with inline keyboard:
    "📝 2 expenses:
     1. Groceries ₪300 — you paid, split 70/30 → partner owes ₪90
     2. Gas ₪200 — you paid, split 70/30 → partner owes ₪60
     Date: [yesterday]
     [✅ Confirm] [✏️ Edit] [❌ Cancel]"

Step 6 — COMMITTING
  User taps ✅ → entries written to ledger
```

### 4.3 Flow Example: Query

```
User: "how much did we spend on groceries this month?"

Step 1 — PARSING
  LLM identifies this as a query, not an expense

Step 2 — TOOL CALL
  Agent calls: query_expenses(category="groceries", date_from="2025-12-01")

Step 3 — RESPONSE
  LLM formats: "This month you spent ₪2,400 on groceries across 8 transactions."
```

### 4.4 Clarification Priority

Clarification and disambiguation is **critical**, especially in early usage. The system should err on the side of asking rather than assuming. Over time, as patterns emerge (e.g., "User A always pays for groceries"), we may add smart defaults — but not for MVP.

---

## 5. Tool Registry

Tools are Python functions with typed schemas that the LLM can call:

| Tool | Purpose | Write? |
|------|---------|--------|
| `parse_expense` | Extract structured expense data from text | No |
| `log_expense` | Commit a validated expense to the ledger | Yes |
| `log_settlement` | Commit a validated settlement | Yes |
| `get_balance` | Derive current balance from ledger | No |
| `query_expenses` | Filter/aggregate expenses | No |
| `list_categories` | Return known categories | No |
| `create_category` | Add a new user-defined category | Yes |
| `validate_settlement` | Check settlement constraints | No |
| `get_recent_entries` | Fetch recent ledger entries (for context/edits) | No |

Each tool has:
- A JSON schema describing its parameters (used by LLM for tool calling)
- Input validation
- Logging of every invocation

---

## 6. Database Schema

### 6.1 Core Tables

```sql
-- Immutable raw inputs (never modified or deleted)
CREATE TABLE raw_inputs (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    telegram_user_id  BIGINT NOT NULL,
    raw_text    TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only financial ledger
CREATE TABLE ledger (
    id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    raw_input_id        UUID NOT NULL REFERENCES raw_inputs(id),
    event_type          TEXT NOT NULL CHECK (event_type IN ('expense', 'settlement', 'correction')),
    amount              DECIMAL(12,2) NOT NULL,
    currency            TEXT NOT NULL DEFAULT 'ILS',
    category            TEXT,
    payer_telegram_id   BIGINT NOT NULL,
    split_payer_pct     DECIMAL(5,2) NOT NULL,
    split_other_pct     DECIMAL(5,2) NOT NULL,
    event_date          DATE NOT NULL,
    description         TEXT,
    tags                TEXT[],
    interpretation_version  INT NOT NULL DEFAULT 1,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_by       UUID REFERENCES ledger(id)
);

-- User-extensible categories
CREATE TABLE categories (
    name        TEXT PRIMARY KEY,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Partner relationship mapping
CREATE TABLE partnerships (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_a_telegram_id  BIGINT NOT NULL,
    user_b_telegram_id  BIGINT NOT NULL,
    default_currency    TEXT NOT NULL DEFAULT 'ILS',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(user_a_telegram_id, user_b_telegram_id)
);
```

### 6.2 Observability Tables

```sql
-- LLM call logging (tracks local vs fallback usage)
CREATE TABLE llm_calls (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    provider        TEXT NOT NULL,          -- 'ollama' or 'claude' or 'openai'
    model           TEXT NOT NULL,
    input_tokens    INT,
    output_tokens   INT,
    latency_ms      INT,
    is_fallback     BOOLEAN NOT NULL DEFAULT false,
    fallback_reason TEXT,                   -- why local model was bypassed
    cost_usd        DECIMAL(8,6),           -- estimated cost for paid calls
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
```

### 6.3 Key Design Properties

- **`raw_inputs`**: Truly immutable. Supports reprocessing requirement.
- **`ledger`**: Append-only. Edits create new rows; old rows get `superseded_by` set.
- **Balance**: Always derived via `SELECT SUM(...)` over active (non-superseded) entries. Never stored.
- **`llm_calls`**: Every LLM interaction logged. Enables reporting on fallback frequency and cost.

---

## 7. Project Structure

```
finbot/
├── docker-compose.yml          # Dev compose (macOS)
├── docker-compose.prod.yml     # Prod compose (Ubuntu + GPU)
├── Dockerfile
├── pyproject.toml              # Dependency management
├── alembic.ini                 # DB migrations config
├── .env.example
│
├── docs/
│   ├── design.md               # This file
│   └── decisions.md            # Architecture Decision Records
│
├── src/
│   └── finbot/
│       ├── __init__.py
│       ├── config.py           # Settings via pydantic-settings
│       │
│       ├── bot/                # Telegram layer (thin)
│       │   ├── __init__.py
│       │   ├── handlers.py     # Message & callback handlers
│       │   ├── keyboards.py    # Inline keyboards (confirm/cancel/edit)
│       │   ├── formatters.py   # Format structured data for Telegram
│       │   └── middleware.py   # Access control & DB session injection
│       │
│       ├── agent/              # LLM orchestration
│       │   ├── __init__.py
│       │   ├── orchestrator.py # Multi-step state machine
│       │   ├── state.py        # Conversation state models & in-memory store
│       │   ├── prompts.py      # System prompts and templates
│       │   └── llm_client.py   # Abstract LLM interface (Ollama / paid API)
│       │
│       ├── tools/              # Tool implementations (called by agent)
│       │   ├── __init__.py
│       │   ├── registry.py     # Tool registry + JSON schemas
│       │   ├── expenses.py     # parse_expense, log_expense
│       │   ├── settlements.py  # log_settlement, validate_settlement
│       │   ├── queries.py      # get_balance, query_expenses
│       │   └── categories.py   # list/create categories
│       │
│       ├── ledger/             # Core accounting (pure logic, no LLM)
│       │   ├── __init__.py
│       │   ├── models.py       # SQLAlchemy ORM models
│       │   ├── repository.py   # DB read/write operations
│       │   ├── balance.py      # Balance derivation from ledger replay
│       │   └── validation.py   # Settlement validation rules
│       │
│       ├── reprocessing/       # Re-parse historical raw inputs (Phase 6)
│       │   └── __init__.py
│       │
│       └── db/
│           ├── __init__.py
│           ├── session.py      # Async DB session factory
│           └── migrations/     # Alembic migration versions
│               └── versions/
│
└── tests/
    ├── test_agent/
    ├── test_bot/
    ├── test_ledger/
    ├── test_tools/
    └── fixtures/
```

### 7.1 Module Boundaries

- **`ledger/`** — Pure accounting. No LLM, no Telegram. Fully unit-testable.
- **`tools/`** — Wraps ledger operations as tool-callable functions with schemas.
- **`agent/`** — Owns the multi-step loop and LLM communication. `llm_client.py` is abstract — swap Ollama for paid API without touching anything else.
- **`bot/`** — Thin Telegram skin. Receives messages, passes to orchestrator, formats responses.

---

## 8. Deployment

### 8.1 Development (macOS)

- Docker Compose with PostgreSQL and Ollama (CPU mode, or no Ollama — use paid API for dev)
- Hot reload via volume mounts
- Local `.env` file

### 8.2 Production (Ubuntu Server)

- Docker Compose managed via Portainer
- GPU passthrough to Ollama container (NVIDIA Container Toolkit)
- PostgreSQL with persistent volume
- Ollama with model volume (persists across container restarts)

### 8.3 Hardware (Production)

| Component | Spec |
|-----------|------|
| CPU | Intel i5-13600 (20 threads) |
| RAM | 32GB DDR4 |
| GPU | NVIDIA RTX 5060 Ti 8GB |
| OS | Ubuntu (Docker host) |

---

## 9. Build Phases

| Phase | Scope | Est. Effort |
|-------|-------|-------------|
| **1. Foundation** | Project scaffold, Docker setup, DB schema + migrations, config | 1-2 days |
| **2. Telegram Bot** | aiogram bot, message reception, raw_input storage, session mgmt | 1 day |
| **3. LLM Integration** | Ollama client, abstract LLM interface, tool schemas, basic parsing | 2-3 days |
| **4. Agent Loop** | Multi-step orchestrator, confirmation flow, clarification, commit | 3-4 days |
| **5. Accounting** | Balance derivation, settlement logging + validation, basic queries | 2-3 days |
| **6. Edit & Reprocess** | Edit flow, superseding entries, reprocessing engine | 2-3 days |
| **7. Hardening** | Error handling, edge cases, prompt tuning, model benchmarking | Ongoing |

---

## 10. Risks & Mitigations

| Risk | Impact | Mitigation |
|------|--------|-----------|
| 7B model misparses ambiguous input | Wrong ledger entries | Spec requires ambiguity blocking. Agent asks rather than assumes. |
| 7B tool calling unreliable | Agent loop breaks | Constrained JSON output + code-side validation. Fallback to paid API. |
| 8GB VRAM too tight for long context | Slow/OOM | Keep context minimal. History in DB, not LLM context. |
| GPU passthrough issues in Docker | Can't run local model | NVIDIA Container Toolkit is mature. Test early in Phase 1. |
| Fallback API costs exceed budget | > $5/month | Logging + alerting on `llm_calls` table. Rate-limit fallbacks. |
