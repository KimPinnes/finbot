# Architecture Decision Records (ADRs)

> FinBot — Natural Language Shared Finance Agent

Each ADR documents a significant technical decision, its context, and rationale.

---

## ADR-001: Telegram Bot as Primary Interface

**Status**: Accepted  
**Date**: 2025-12

### Context

The product needs to be available on Android, iOS, and desktop with minimal development overhead. Building native apps or a web app would require significant frontend work and app-store management.

### Decision

Use a **Telegram Bot** as the sole user interface.

### Rationale

- Telegram is available on all target platforms (Android, iOS, desktop, web)
- Zero app-store overhead — no review process, no signing, no distribution
- Rich bot API supports inline keyboards (for confirmations), formatted messages, and media
- Users interact via natural language, which maps directly to a chat interface
- Mature Python libraries (aiogram 3.x) for async bot development

### Consequences

- Dependent on Telegram's platform availability and API stability
- Limited UI richness compared to a native app (no charts, limited layout control)
- Users must have a Telegram account

---

## ADR-002: Custom Agent Loop Over LangGraph {#adr-002-custom-agent-loop-over-langgraph}

**Status**: Accepted  
**Date**: 2025-12

### Context

The agent needs multi-step reasoning: parse → validate → clarify → confirm → commit. LangGraph is a framework specifically designed for stateful, multi-step agent workflows with branching.

### Decision

Build a **custom lightweight state machine** for the agent orchestrator. Do not use LangChain or LangGraph for MVP.

### Rationale

- The agent flow is relatively linear with a small number of states (~6 states)
- LangGraph adds significant dependency weight and abstraction overhead
- Custom implementation is more transparent, debuggable, and easier to reason about
- The modular project structure allows migration to LangGraph later if complexity grows
- LangChain base library adds dependency bloat without sufficient value for this use case
- Direct Ollama client + custom tool registry is cleaner and lighter

### Consequences

- Must implement state management, error recovery, and retry logic ourselves
- If flow complexity grows significantly (many branching paths), should reconsider LangGraph
- Team must maintain the orchestrator code rather than relying on framework updates

### Migration Path

The `agent/orchestrator.py` is isolated. If we outgrow the custom solution:
1. Keep `tools/` and `ledger/` unchanged
2. Replace `orchestrator.py` with a LangGraph graph definition
3. Adapt `llm_client.py` to LangGraph's LLM interface

---

## ADR-003: Qwen2.5-7B as Primary Local LLM {#adr-003-qwen25-7b-as-primary-local-llm}

**Status**: Accepted  
**Date**: 2025-12

### Context

Budget constraint: < $5/month for LLM costs. Hardware: RTX 5060 Ti with 8GB VRAM, i5-13600, 32GB RAM. Need reliable tool/function calling and structured data extraction.

### Decision

Use **Qwen2.5-7B-Instruct** (Q4_K_M quantization) via Ollama as the primary LLM. Use a paid API (Claude Haiku or GPT-4o-mini) as a logged fallback.

### Rationale

**Why Qwen2.5-7B:**
- Q4_K_M quantization uses ~5.5GB VRAM — fits the 8GB GPU with headroom for KV cache
- Native tool/function calling support (critical for agent loop)
- Excellent at structured extraction tasks, which is the primary workload
- Expected 30-60 tokens/sec on the target hardware
- Strong multilingual support (though Hebrew is deprioritized vs performance)

**Why not larger models:**
- 14B models at Q4 need ~9.5GB — doesn't fit in 8GB VRAM without CPU offload (slow)
- 70B+ models are not feasible on this hardware

**Why not thinking models locally:**
- DeepSeek-R1-Distill-Qwen-7B has reasoning ability but unreliable tool calling
- Multi-step reasoning is handled by the agent loop in code, not LLM chain-of-thought
- A reliable tool-calling model is more valuable than a thinking model for this use case

**Fallback model:**
- Claude Haiku / GPT-4o-mini at ~$0.25/1M input tokens
- Expected fallback rate < 20% → estimated cost < $1/month

### Alternatives Considered

| Model | VRAM | Tool Calling | Verdict |
|-------|------|-------------|---------|
| Qwen2.5-7B-Instruct | 5.5GB | ✅ Native | **Selected** |
| Llama 3.1 8B Instruct | 5.5GB | ✅ Native | Good alternative, less strong at structured extraction |
| DeepSeek-R1-Distill-7B | 5.5GB | ⚠️ Unreliable | Thinking model, but poor tool calling |
| Phi-3.5-mini (3.8B) | 3GB | ⚠️ Limited | Fast but less capable |
| Qwen2.5-14B-Instruct | 9.5GB | ✅ Native | Doesn't fit GPU |

### Consequences

- Must handle cases where 7B model produces low-quality outputs → fallback or clarification
- System prompt engineering is critical to maximize 7B performance
- Regular evaluation against paid API quality to ensure acceptable parsing accuracy
- Model can be swapped via config (Ollama model name) without code changes

---

## ADR-004: Append-Only Ledger with Derived Balances {#adr-004-append-only-ledger}

**Status**: Accepted  
**Date**: 2025-12

### Context

The product specification requires immutable audit trails, reprocessability, and correct financial state. Traditional mutable CRUD patterns risk data loss and make auditing difficult.

### Decision

Implement an **append-only ledger** where balances are always **derived** (never stored as authoritative state).

### Rationale

- Matches the product spec's core principles (immutable input, derivable state)
- Enables full audit trail — every financial event is preserved
- Reprocessing is safe: create new interpretations, mark old ones as superseded
- No risk of inconsistent state from partial updates
- Simple mental model: balance = sum of all active ledger entries

### Consequences

- Balance queries require aggregation over the full ledger (mitigated by PostgreSQL performance — acceptable for 2-person use case)
- Edits are slightly more complex (create new entry + mark old as superseded) vs simple UPDATE
- Storage grows over time (negligible for expected volume)

---

## ADR-005: PostgreSQL as Database {#adr-005-postgresql}

**Status**: Accepted  
**Date**: 2025-12

### Context

Need a reliable database for financial data with support for append-only patterns, array types (tags), and decimal precision.

### Decision

Use **PostgreSQL 16** (Dockerized, Alpine image).

### Rationale

- Robust ACID transactions — critical for financial data
- Native `DECIMAL` type for precise money arithmetic
- Native array types (`TEXT[]`) for tags
- `UUID` generation (`gen_random_uuid()`)
- Excellent Docker support with persistent volumes
- User already runs Docker stacks via Portainer
- SQLite was considered but lacks concurrent access and advanced types

### Consequences

- Requires a running PostgreSQL container (small resource footprint)
- Schema migrations managed via Alembic

---

## ADR-006: LLM Fallback Logging & Reporting {#adr-006-fallback-logging}

**Status**: Accepted  
**Date**: 2025-12

### Context

Paid API is used as a fallback when the local model fails or produces low-confidence results. Budget is capped at $5/month. Need visibility into fallback frequency to prevent cost overruns and identify improvement opportunities.

### Decision

Log **every LLM call** (local and paid) to a dedicated `llm_calls` table with provider, model, token counts, latency, fallback flag, reason, and estimated cost.

### Rationale

- Enables cost tracking and budget alerting
- Identifies patterns where local model consistently fails → improve prompts or fine-tune
- Provides latency data for performance monitoring
- Simple SQL queries can generate reports:
  ```sql
  -- Monthly fallback rate
  SELECT
    date_trunc('month', created_at) AS month,
    COUNT(*) FILTER (WHERE is_fallback) AS fallback_calls,
    COUNT(*) AS total_calls,
    ROUND(100.0 * COUNT(*) FILTER (WHERE is_fallback) / COUNT(*), 1) AS fallback_pct,
    SUM(cost_usd) AS total_cost_usd
  FROM llm_calls
  GROUP BY 1 ORDER BY 1;
  ```

### Consequences

- Small write overhead per LLM call (negligible)
- Must estimate cost per call (token-based calculation from provider pricing)
- Reporting can be queried directly or exposed as a bot command (`/stats`)

---

## ADR-007: Clarification-First Approach {#adr-007-clarification-first}

**Status**: Accepted  
**Date**: 2025-12

### Context

The product spec mandates that ambiguous inputs must block commit. Free-text parsing is inherently probabilistic. A 7B model will have lower accuracy on edge cases than a large paid model.

### Decision

Adopt a **clarification-first** strategy: the system should err on the side of asking the user rather than making assumptions, especially in early usage.

### Rationale

- Financial correctness is paramount — a wrong entry is worse than an extra question
- User trust is built by confirming before committing, not by guessing
- The 7B model's limitations are compensated by explicit clarification
- Over time, patterns may be learned (e.g., "User A always pays for groceries"), but this is explicitly deferred past MVP

### Consequences

- Users may experience more back-and-forth initially
- Agent prompts must be designed to detect low confidence and trigger clarification
- Confirmation step (inline keyboard) is mandatory for all write operations

### Required Fields for Commit

No expense is committed unless ALL of these are resolved:

| Field | Rule |
|-------|------|
| Amount | Mandatory, explicit in input |
| Currency | Default: ILS |
| Date | Default: today, unless specified |
| Category | Mandatory — inferred or asked |
| Payer | Mandatory — must be explicit |
| Split | Mandatory — must resolve to percentages summing to 100% |
| Participants | Default: both partners |

---

## ADR-008: Python with Async Architecture {#adr-008-python-async}

**Status**: Accepted  
**Date**: 2025-12

### Context

Need to choose a programming language and concurrency model. The system handles Telegram webhooks, LLM calls (potentially slow), and database queries concurrently.

### Decision

Use **Python 3.12+** with **async/await** throughout (asyncio).

### Rationale

- Best ecosystem for LLM integration (Ollama client, OpenAI/Anthropic SDKs)
- aiogram 3.x is async-native
- asyncpg / SQLAlchemy async for non-blocking DB access
- Team familiarity
- Fast iteration speed for an evolving project

### Key Libraries

| Library | Purpose | Version |
|---------|---------|---------|
| aiogram | Telegram bot framework | 3.x |
| ollama | Ollama Python client | latest |
| anthropic / openai | Paid API fallback | latest |
| sqlalchemy[asyncio] | ORM + async DB | 2.x |
| asyncpg | PostgreSQL async driver | latest |
| alembic | DB migrations | latest |
| pydantic-settings | Configuration management | 2.x |
| pydantic | Data validation & schemas | 2.x |

---

## ADR-009: Docker Compose Deployment {#adr-009-docker-deployment}

**Status**: Accepted  
**Date**: 2025-12

### Context

User manages multiple Docker stacks on Ubuntu via Portainer. Development is on macOS.

### Decision

Use **Docker Compose** with separate dev and prod configurations.

### Rationale

- Consistent with user's existing infrastructure management
- Dev compose: PostgreSQL + app (CPU mode, or paid API during dev)
- Prod compose: PostgreSQL + app + Ollama with GPU passthrough
- Portainer can import compose files directly

### Prod Compose Services

| Service | Image | Notes |
|---------|-------|-------|
| `finbot` | Custom (Python) | The bot + agent application |
| `db` | postgres:16-alpine | Persistent volume for data |
| `ollama` | ollama/ollama | GPU passthrough, persistent model volume |

### GPU Passthrough

Requires NVIDIA Container Toolkit on the Ubuntu host. The 5060 Ti should be supported with recent drivers.

---

## ADR-010: Identity via Telegram User ID {#adr-010-identity}

**Status**: Accepted  
**Date**: 2025-12

### Context

Need to identify the two partners in shared expenses. Options: hardcoded names, Telegram user IDs, or custom auth.

### Decision

Use **Telegram user IDs** as the primary identity mechanism. Partners are linked via a `partnerships` table.

### Rationale

- Zero-friction: no separate login or registration
- Telegram provides stable user IDs
- Each partner sends from their own Telegram account
- The `partnerships` table links two Telegram users as finance partners
- Messages can still reference the other partner by name or "partner" keyword

### Consequences

- Both partners must have Telegram accounts
- Partnership setup is a one-time operation (could be a `/setup` command)
- If a user says "I paid" — we know who "I" is from Telegram user ID
- If a user says "partner paid" — we look up the other user in the partnership
