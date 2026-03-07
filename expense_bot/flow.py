# expense_bot/flow.py — multi-turn state machine
#
# Handles two flows:
#   1. Statement import: user triggers → bot asks for text → LLM parses →
#      bot shows new merchants with inline keyboard for category confirmation →
#      user confirms each → expenses saved.
#
#   2. CSV import: bot receives document → parses CSV rows →
#      same category-confirmation flow as above.
#
# State per chat_id (in-memory only — survives as long as the process runs):
#   mode: "awaiting_statement" | "awaiting_csv" | "reviewing"
#   pending_expenses: list of parsed expense dicts (merchant/amount/currency/date/category)
#   new_merchants: list of merchant names that were not in DB
#   review_idx: index into new_merchants currently being reviewed
#   source: "statement" | "csv"

import io
import csv
import db
import parser as expense_parser
import executor
from config import CATEGORIES, DEFAULT_CATEGORY

# ── State storage ──────────────────────────────────────────────────────────────

_state: dict[int, dict] = {}


def is_active(chat_id: int) -> bool:
    return chat_id in _state


def cancel(chat_id: int):
    _state.pop(chat_id, None)


def current_mode(chat_id: int) -> str | None:
    return _state.get(chat_id, {}).get("mode")


# ── Flow entry points ──────────────────────────────────────────────────────────

def start_statement_import(chat_id: int) -> str:
    """Called when user signals they want to import a statement."""
    _state[chat_id] = {"mode": "awaiting_statement", "source": "statement"}
    return (
        "📋 *ייבוא חשבון אשראי*\n\n"
        "העתק והדבק את טקסט החשבון כאן.\n"
        "אני אחלץ את ההוצאות אוטומטית.\n\n"
        "לביטול: /cancel"
    )


def start_csv_import(chat_id: int) -> str:
    """Called when user sends a document/CSV."""
    _state[chat_id] = {"mode": "awaiting_csv", "source": "csv"}
    return "📂 שלח את קובץ ה-CSV עם ההוצאות.\nלביטול: /cancel"


# ── Text handler (called from bot.py for text messages) ───────────────────────

def handle_text(chat_id: int, text: str) -> str:
    """Route text to the correct flow step."""
    state = _state.get(chat_id)
    if not state:
        return ""

    mode = state["mode"]

    if mode == "awaiting_statement":
        return _parse_statement_text(chat_id, text)

    return ""  # other modes handled elsewhere


# ── CSV handler (called from bot.py after reading document bytes) ──────────────

def handle_csv(chat_id: int, file_bytes: bytes) -> str:
    """Parse a CSV file and start the category-review flow."""
    try:
        text = file_bytes.decode("utf-8-sig")  # handle BOM from Excel exports
    except UnicodeDecodeError:
        text = file_bytes.decode("windows-1255", errors="replace")  # Hebrew Windows encoding

    rows = list(csv.DictReader(io.StringIO(text)))
    if not rows:
        cancel(chat_id)
        return "הקובץ ריק או בפורמט לא מוכר."

    expenses = _parse_csv_rows(rows)
    if not expenses:
        cancel(chat_id)
        return (
            "לא הצלחתי לקרוא הוצאות מהקובץ.\n"
            "ודא שהעמודות הן: date, merchant, amount (ואופציונלית currency)"
        )

    return _start_review(chat_id, expenses, source="csv")


# ── Callback handler (inline keyboard button presses) ─────────────────────────

def handle_callback(chat_id: int, callback_data: str) -> tuple[str, bool]:
    """
    Handle a category-selection callback.
    Returns (reply_text, done).
      done=True → all merchants reviewed, expenses saved.
      done=False → more merchants to review.
    callback_data format: "setcat:{cat_index}" or "skipcat"
    """
    state = _state.get(chat_id)
    if not state or state.get("mode") != "reviewing":
        return ("אין פעולה פעילה.", True)

    new_merchants = state["new_merchants"]
    idx = state["review_idx"]

    if idx >= len(new_merchants):
        return _finish_review(chat_id)

    merchant_name = new_merchants[idx]

    if callback_data == "skipcat":
        # Keep the LLM-suggested category — no change needed
        pass
    elif callback_data.startswith("setcat:"):
        try:
            cat_idx = int(callback_data.split(":")[1])
            chosen_category = CATEGORIES[cat_idx]
        except (IndexError, ValueError):
            return ("שגיאה בבחירת קטגוריה.", False)
        # Update all pending expenses for this merchant
        for exp in state["pending_expenses"]:
            if exp["merchant"].strip().lower() == merchant_name.strip().lower():
                exp["category"] = chosen_category

    state["review_idx"] = idx + 1

    # Show next merchant or finish
    if state["review_idx"] >= len(new_merchants):
        return _finish_review(chat_id)

    next_merchant = new_merchants[state["review_idx"]]
    next_expense = next(
        (e for e in state["pending_expenses"]
         if e["merchant"].strip().lower() == next_merchant.strip().lower()),
        None
    )
    suggested = next_expense["category"] if next_expense else DEFAULT_CATEGORY
    return (_review_prompt(next_merchant, suggested), False)


# ── Internal helpers ───────────────────────────────────────────────────────────

def _parse_statement_text(chat_id: int, text: str) -> str:
    known = {m["display"]: m["category"] for m in db.get_all_merchants()}
    expenses = expense_parser.parse_statement(text, known)
    if not expenses:
        cancel(chat_id)
        return "לא מצאתי הוצאות בטקסט. נסה שוב או /cancel לביטול."
    return _start_review(chat_id, expenses, source="statement")


def _parse_csv_rows(rows: list[dict]) -> list[dict]:
    """
    Try to extract expenses from CSV rows.
    Accepts flexible column names (Hebrew / English).
    """
    expenses = []
    # Map common column name variants
    col_map = {
        "merchant": ["merchant", "בית עסק", "עסק", "תיאור", "description", "name"],
        "amount":   ["amount", "סכום", "חיוב", "charge", "debit"],
        "date":     ["date", "תאריך", "תאריך עסקה"],
        "currency": ["currency", "מטבע"],
    }

    def find_col(row: dict, key: str) -> str | None:
        for candidate in col_map[key]:
            for col in row.keys():
                if col.strip().lower() == candidate.lower():
                    return col
        return None

    for row in rows:
        merchant_col = find_col(row, "merchant")
        amount_col   = find_col(row, "amount")
        if not merchant_col or not amount_col:
            continue
        merchant = str(row.get(merchant_col, "")).strip()
        if not merchant:
            continue
        try:
            # Remove currency symbols and commas
            raw_amount = str(row.get(amount_col, "0")).replace(",", "").replace("₪", "").strip()
            amount = float(raw_amount)
        except ValueError:
            continue
        if amount <= 0:
            continue

        date_col = find_col(row, "date")
        raw_date = str(row.get(date_col, "")).strip() if date_col else ""
        expense_date = expense_parser._parse_date(raw_date) if raw_date else None

        currency_col = find_col(row, "currency")
        currency = str(row.get(currency_col, "ILS")).strip().upper() if currency_col else "ILS"
        if currency not in {"ILS", "USD", "EUR", "GBP"}:
            currency = "ILS"

        expenses.append({
            "merchant": merchant,
            "amount": amount,
            "currency": currency,
            "date": expense_date,
            "category": DEFAULT_CATEGORY,  # resolved later
        })

    return expenses


def _start_review(chat_id: int, expenses: list[dict], source: str) -> str:
    """
    Resolve categories: use DB for known merchants, ask LLM for the rest.
    If there are new merchants, start interactive review flow.
    Otherwise save immediately.
    """
    known = {m["display"].strip().lower(): m["category"] for m in db.get_all_merchants()}
    new_merchants: list[str] = []

    for exp in expenses:
        key = exp["merchant"].strip().lower()
        if key in known:
            exp["category"] = known[key]
        else:
            # LLM-suggest a category
            suggested = expense_parser.suggest_category(exp["merchant"])
            exp["category"] = suggested
            # Track unique new merchants
            if exp["merchant"] not in new_merchants:
                new_merchants.append(exp["merchant"])

    total_ils = sum(e["amount"] for e in expenses if e.get("currency", "ILS") == "ILS")

    if not new_merchants:
        # All merchants known — save immediately
        cancel(chat_id)
        reply = executor.handle_add_batch(expenses, source=source)
        return reply

    # Update state to reviewing phase
    _state[chat_id] = {
        "mode": "reviewing",
        "pending_expenses": expenses,
        "new_merchants": new_merchants,
        "review_idx": 0,
        "source": source,
    }

    first_merchant = new_merchants[0]
    first_exp = next(
        (e for e in expenses if e["merchant"].strip().lower() == first_merchant.strip().lower()),
        None
    )
    suggested = first_exp["category"] if first_exp else DEFAULT_CATEGORY

    header = (
        f"מצאתי *{len(expenses)}* הוצאות (סה\"כ {executor.format_amount(total_ils)})\n"
        f"יש *{len(new_merchants)}* בתי עסק חדשים — אשר את הקטגוריה לכל אחד:\n\n"
    )
    return header + _review_prompt(first_merchant, suggested)


def _review_prompt(merchant: str, suggested_category: str) -> str:
    """Return the text for a single category-review step (inline keyboard shown separately)."""
    return (
        f"🏪 *{merchant}*\n"
        f"קטגוריה מוצעת: _{suggested_category}_\n\n"
        "בחר קטגוריה מהכפתורים למטה, או לחץ ✓ לאישור המוצעת."
    )


def _finish_review(chat_id: int) -> tuple[str, bool]:
    state = _state.pop(chat_id, {})
    expenses = state.get("pending_expenses", [])
    source   = state.get("source", "statement")
    if not expenses:
        return ("לא נשמרו הוצאות.", True)
    reply = executor.handle_add_batch(expenses, source=source)
    return (reply, True)


# ── Keyboard builder (used by bot.py) ─────────────────────────────────────────

def build_category_keyboard():
    """
    Return list-of-lists suitable for InlineKeyboardMarkup.
    Each row has 2 buttons. Last row has the "approve suggested" button.
    """
    from telegram import InlineKeyboardButton

    rows = []
    row = []
    for i, cat in enumerate(CATEGORIES):
        row.append(InlineKeyboardButton(cat, callback_data=f"setcat:{i}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)

    rows.append([InlineKeyboardButton("✓ אשר קטגוריה מוצעת", callback_data="skipcat")])
    return rows
