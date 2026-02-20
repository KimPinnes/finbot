# User-Facing Actions (Telegram)

This document should stay in sync with what users see in Telegram: menu button, `/` commands, and in-bot help. When you change commands or help text in the bot, update this file and the README “Bot Commands” table.

---

## Commands (Telegram “/” menu)

| Command      | Description                    |
|-------------|--------------------------------|
| `/start`    | Show welcome message           |
| `/help`     | Show usage guide               |
| `/balance`  | Show current balance           |
| `/setup <partner_id>` | Link with your partner (one-time) |
| `/categories` | View and rename categories   |
| `/add`      | Open expense form (Mini App)   |

---

## Menu button

- **Add Expense** — Opens the Mini App (category picker → amount, split, date). Only works inside Telegram; in a normal browser you get “Access denied”.

---

## Natural language (via chat)

- **Expenses** — e.g. *“Coffee 25 shekels”*, *“I paid 100 for groceries, split half”*
- **Settlements** — e.g. *“She paid me back 50”*, *“Settled in full”* / *“all”*
- **Queries** — e.g. *“What did we spend on food this month?”*
- **Categories** — Use `/categories`; type *cancel* to abort a rename.

The agent parses messages, asks for confirmation when needed, then commits. Use inline keyboards to confirm or cancel.
