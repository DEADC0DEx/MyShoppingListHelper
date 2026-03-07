# Expense Bot — Personal Expense Tracker

A local expense-tracking system with two interfaces:

| Interface | Use case |
|---|---|
| **Telegram bot** | Log expenses in real time (e.g. via Apple Shortcut + SMS) |
| **Local web UI** | Upload the monthly credit-card statement |

Both write to the same SQLite database. Duplicate detection prevents double-counting when an SMS and a statement describe the same charge.

---

## Setup

### 1. Install dependencies

```bash
cd expense_bot
pip install -r requirements.txt
```

### 2. Configure `config.py`

| Setting | Description |
|---|---|
| `TELEGRAM_TOKEN` | Bot token from [@BotFather](https://t.me/BotFather) |
| `OLLAMA_URL` | Ollama API endpoint (default: `localhost:11434`) |
| `OLLAMA_MODEL` | Hebrew-capable model (default: `aminadaven/dictalm2.0-instruct`) |
| `DB_PATH` | SQLite file path (default: `data/expenses.db`) |
| `CATEGORIES` | Your expense categories (edit to match your spreadsheet) |

### 3. Pull the LLM model

```bash
ollama pull aminadaven/dictalm2.0-instruct:q4_k_m
```

---

## Running

### Telegram bot

```bash
python expense_bot/bot.py
```

### Local web UI (monthly statement upload)

```bash
python expense_bot/web_ui.py
# Open http://localhost:5000
```

Both can run simultaneously — they share the same database.

---

## Telegram bot commands

| What you write / command | What happens |
|---|---|
| `שילמתי 150 ₪ ברמי לוי` | Logs a single expense |
| `/import_statement` | Paste raw statement text for bulk import |
| Send a `.csv` file | Bulk import from CSV |
| `/expenses` | This month's expenses |
| `/expenses 03 2025` | Specific month |
| `/summary` | Totals by category |
| `/set_category רמי לוי \| אוכל` | Remap a merchant's category |
| `/merchants` | List known merchants |
| `/cancel` | Cancel current action |

---

## Web UI — statement import flow

1. **Upload** — choose a CSV file **or** paste raw text (copy-paste from PDF)
2. **Review** — table shows every parsed expense:
   - Category dropdown (pre-filled from history or LLM suggestion)
   - **"כבר קיים"** badge on rows that likely duplicate an existing entry
   - Checkbox per row — duplicates are pre-unchecked
3. **Save** — only checked rows are written to the database

### CSV column names (Hebrew or English accepted)

| Field | Accepted column headers |
|---|---|
| Date | `date`, `תאריך`, `תאריך עסקה` |
| Merchant | `merchant`, `בית עסק`, `עסק`, `תיאור`, `description` |
| Amount | `amount`, `סכום`, `חיוב`, `charge`, `debit` |
| Currency | `currency`, `מטבע` |

Encoding: UTF-8 or Windows-1255 (Excel Hebrew export). BOM handled automatically.

---

## Duplicate detection

When importing a statement, each expense is compared to the database:

- **Merchant** — normalized (lowercase, stripped)
- **Amount** — within ±0.01
- **Date** — within ±7 days (SMS = transaction date, statement = billing date)

Duplicates are flagged in the web UI and skipped automatically in the Telegram import flow.

---

## Apple Shortcut — SMS forwarding

Set up a Shortcut that triggers on bank/credit-card SMS messages and forwards the text to the Telegram bot. The bot parses the Hebrew text and logs the expense automatically.

---

## File structure

```
expense_bot/
├── bot.py           # Telegram entry point
├── web_ui.py        # Local Flask UI (localhost:5000)
├── flow.py          # Multi-turn state machine (import + category review)
├── parser.py        # LLM-powered parsing (Ollama)
├── executor.py      # Business logic — no Telegram, no LLM
├── db.py            # SQLite helpers + duplicate detection
├── config.py        # Tokens, model, categories, DB path
├── requirements.txt
└── templates/       # HTML templates for the web UI
    ├── base.html
    ├── upload.html
    ├── review.html
    └── done.html
```

Data is stored in `data/expenses.db` (auto-created on first run).
