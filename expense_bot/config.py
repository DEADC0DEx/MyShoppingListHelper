# expense_bot/config.py — all constants

TELEGRAM_TOKEN = "YOUR_EXPENSE_BOT_TOKEN"

OLLAMA_URL = "http://localhost:11434/api/generate"
OLLAMA_MODEL = "aminadaven/dictalm2.0-instruct:q4_k_m"

DB_PATH = "data/expenses.db"

# ── Categories ─────────────────────────────────────────────────────────────────
# Replace this list with the categories from your Excel file.
# Order matters — shown in this order in inline keyboards.
CATEGORIES = [
    "אוכל וסופרמרקט",
    "מסעדות וקפה",
    "תחבורה",
    "בית ומשק בית",
    "בריאות ורפואה",
    "בידור ופנאי",
    "ביגוד והנעלה",
    "מנויים ושירותים",
    "חינוך",
    "ביטוח",
    "תקשורת",
    "שונות",
]

DEFAULT_CATEGORY = "שונות"

# Actions the intent parser can return
VALID_ACTIONS = {
    "add_expense",       # single expense from free text
    "import_statement",  # user wants to paste/upload a statement
    "view_expenses",     # list this month's expenses
    "view_summary",      # totals by category
    "set_category",      # manually remap a merchant
    "unknown",
}
