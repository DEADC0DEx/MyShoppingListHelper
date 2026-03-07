# expense_bot/executor.py — pure business logic, no Telegram, no LLM
#
# All functions return a Hebrew string ready to send back to the user.

from datetime import date
import db
from config import CATEGORIES


def format_amount(amount: float, currency: str = "ILS") -> str:
    sym = {"ILS": "₪", "USD": "$", "EUR": "€", "GBP": "£"}.get(currency, currency)
    if currency == "ILS":
        return f"{amount:,.2f} {sym}"
    return f"{sym}{amount:,.2f}"


def handle_add(expense: dict) -> str:
    """
    Save a single validated expense and return a confirmation string.
    expense keys: merchant, amount, currency, date, category
    """
    exp_id = db.add_expense(
        raw_merchant=expense["merchant"],
        amount=expense["amount"],
        category=expense["category"],
        expense_date=expense.get("date"),
        currency=expense.get("currency", "ILS"),
        source="manual",
    )
    date_str = f" ({expense['date']})" if expense.get("date") else ""
    return (
        f"✅ נרשמה הוצאה #{exp_id}\n"
        f"  בית עסק: {expense['merchant']}\n"
        f"  סכום: {format_amount(expense['amount'], expense.get('currency','ILS'))}{date_str}\n"
        f"  קטגוריה: {expense['category']}"
    )


def handle_add_batch(expenses: list[dict], source: str = "statement") -> str:
    """
    Save a list of validated expenses (all categories already resolved).
    Returns summary string.
    """
    normalized = [
        {
            "raw_merchant": e["merchant"],
            "amount": e["amount"],
            "category": e["category"],
            "expense_date": e.get("date"),
            "currency": e.get("currency", "ILS"),
            "note": e.get("note"),
        }
        for e in expenses
    ]
    count = db.add_expenses_batch(normalized, source=source)
    total_ils = sum(e["amount"] for e in expenses if e.get("currency", "ILS") == "ILS")
    return (
        f"✅ נשמרו {count} הוצאות\n"
        f"סה\"כ: {format_amount(total_ils)}"
    )


def handle_view(year: int | None = None, month: int | None = None) -> str:
    today = date.today()
    y = year or today.year
    m = month or today.month
    expenses = db.get_expenses(y, m)
    if not expenses:
        return f"אין הוצאות רשומות ל-{m:02d}/{y}."
    month_name = _month_name(m)
    lines = [f"💳 *הוצאות {month_name} {y}* ({len(expenses)} פריטים)\n"]
    for e in expenses:
        date_tag = f"`{e['expense_date']}`  " if e.get("expense_date") else ""
        lines.append(
            f"{date_tag}*{e['raw_merchant']}*  {format_amount(e['amount'], e['currency'])}\n"
            f"  _{e['category']}_"
        )
    return "\n".join(lines)


def handle_summary(year: int | None = None, month: int | None = None) -> str:
    today = date.today()
    y = year or today.year
    m = month or today.month
    summary = db.get_summary(y, m)
    if not summary:
        return f"אין הוצאות רשומות ל-{m:02d}/{y}."
    total = sum(summary.values())
    month_name = _month_name(m)
    lines = [f"📊 *סיכום הוצאות {month_name} {y}*\n"]
    for cat in CATEGORIES:
        if cat in summary:
            pct = summary[cat] / total * 100
            lines.append(f"  {cat}: {format_amount(summary[cat])} ({pct:.0f}%)")
    # catch categories not in CATEGORIES list
    for cat, amt in summary.items():
        if cat not in CATEGORIES:
            pct = amt / total * 100
            lines.append(f"  {cat}: {format_amount(amt)} ({pct:.0f}%)")
    lines.append(f"\n*סה\"כ: {format_amount(total)}*")
    return "\n".join(lines)


def handle_set_category(merchant: str, category: str) -> str:
    if category not in CATEGORIES:
        cat_list = "\n".join(f"  • {c}" for c in CATEGORIES)
        return f"קטגוריה לא מוכרת: '{category}'\n\nקטגוריות זמינות:\n{cat_list}"
    db.upsert_merchant(merchant, category)
    return f"✅ בית העסק *{merchant}* שויך לקטגוריה *{category}*"


def handle_merchants() -> str:
    merchants = db.get_all_merchants()
    if not merchants:
        return "אין בתי עסק רשומים עדיין."
    lines = ["🏪 *בתי עסק ידועים:*\n"]
    for m in merchants:
        lines.append(f"  {m['display']} — _{m['category']}_")
    return "\n".join(lines)


# ── Internal ───────────────────────────────────────────────────────────────────

_MONTH_NAMES = [
    "", "ינואר", "פברואר", "מרץ", "אפריל", "מאי", "יוני",
    "יולי", "אוגוסט", "ספטמבר", "אוקטובר", "נובמבר", "דצמבר"
]

def _month_name(m: int) -> str:
    return _MONTH_NAMES[m] if 1 <= m <= 12 else str(m)
