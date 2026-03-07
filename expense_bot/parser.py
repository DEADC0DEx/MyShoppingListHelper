# expense_bot/parser.py — the ONLY place the LLM is called
#
# Design rules (same as shopping bot):
#   1. Validate all LLM output in Python — never trust JSON directly
#   2. On any failure return safe default
#   3. Hebrew enforced in every prompt
#   4. One Ollama call per user message

import json
import re
import requests
from datetime import date
from config import OLLAMA_URL, OLLAMA_MODEL, CATEGORIES, DEFAULT_CATEGORY

# ── Prompts ────────────────────────────────────────────────────────────────────

_CATEGORY_LIST = "\n".join(f'- "{c}"' for c in CATEGORIES)

_SINGLE_SYSTEM = f"""אתה עוזר לניהול הוצאות. אתה מחזיר JSON בלבד.
אסור לכתוב טקסט נוסף, הסברים, או markdown.

הסכמה:
{{"action":"add_expense","merchant":"<שם בית העסק>","amount":<מספר>,"currency":"ILS","date":"<תאריך כפי שמופיע בטקסט, או null>","category":"<קטגוריה>"}}

קטגוריות מותרות:
{_CATEGORY_LIST}

חוקים:
- merchant: שם בית העסק כפי שהוזכר
- amount: מספר חיובי בלבד (ללא סימן מטבע)
- currency: "ILS" אלא אם צוין מטבע אחר (USD/EUR וכו')
- date: העתק את התאריך **בדיוק כפי שמופיע בטקסט** (לדוגמה "04/03" או "04/03/2026") — אל תמיר לפורמט אחר. אם אין תאריך, החזר null.
- category: בחר את המתאימה ביותר מהרשימה
- action: תמיד "add_expense"
- אם זה לא הוצאה ספציפית, החזר {{"action":"unknown"}}

פורמטים נפוצים של SMS אישור עסקה (חלץ merchant ו-amount מהם):
- "אישור עסקה: חויבת ב-[עסק] בסך ₪[סכום] בתאריך [תאריך]. קוד אישור: [מספר]"
- "תודה על קנייתך ב-[עסק]! הזמנתך אושרה. סכום: ₪[סכום]. מספר הזמנה: [מספר]"
- "שלום [שם], התשלום עבור [תיאור] בסך ₪[סכום] בוצע בהצלחה"
- "חויבת בסך [סכום] ₪ ב[עסק] ב[תאריך]"
- "Max: אישור עסקה ב[עסק] על סך [סכום] ₪"
- "שלום, בכרטיסך [4 ספרות] אושרה עסקה ב-[DD/MM] בסך [סכום] ש"ח ב[עסק]" — ישראכרט
  → merchant: שם העסק שמופיע אחרי "ב" בסוף המשפט; date: העתק את DD/MM מהטקסט כמות שהוא
- "בכרטיס מסטרקארד שמסתיים ב[ספרות] קיבלנו בתאריך [DD/MM] בקשה לעסקה בחו"ל ב[עסק] בסך [סכום] שח" — כאל
  → merchant: שם העסק; amount: הסכום בשקלים; date: העתק את DD/MM מהטקסט כמות שהוא
בכל הפורמטים האלה, זו הוצאה תקינה — אל תחזיר unknown."""

_STATEMENT_SYSTEM = f"""אתה עוזר לניהול הוצאות. אתה מחזיר JSON בלבד.
אסור לכתוב טקסט נוסף, הסברים, או markdown.

קיבלת קטע מחשבון אשראי. חלץ את כל ההוצאות לרשימת JSON:
{{"action":"import_statement","expenses":[{{"merchant":"...","amount":0.0,"currency":"ILS","date":"YYYY-MM-DD"}}]}}

חוקים:
- חלץ כל שורת הוצאה — אל תדלג על שורות
- amount: מספר חיובי בלבד, ב-ILS אלא אם יש סימן מטבע אחר
- date: העתק את התאריך **בדיוק כפי שמופיע בחשבון** (לדוגמה "04/03" או "04/03/26") — אל תמיר לפורמט אחר. אם אין תאריך, null.
- merchant: שם בית העסק כפי שמופיע בחשבון
- אל תמציא הוצאות שלא מופיעות בטקסט
- אם אין הוצאות בטקסט, החזר {{"action":"import_statement","expenses":[]}}"""

_INTENT_SYSTEM = f"""אתה עוזר לניהול הוצאות. אתה מחזיר JSON בלבד.
אסור לכתוב טקסט נוסף, הסברים, או markdown.

{{"action":"<פעולה>"}}

פעולות מותרות:
- "add_expense"       — המשתמש מדווח על הוצאה, כולל SMS אישור עסקה מהבנק/מקס/ויזה
- "import_statement"  — המשתמש רוצה להכניס חשבון אשראי (מרובה עסקאות)
- "view_expenses"     — המשתמש רוצה לראות הוצאות
- "view_summary"      — המשתמש רוצה סיכום לפי קטגוריות
- "set_category"      — המשתמש רוצה לשנות קטגוריה לבית עסק
- "unknown"           — לא ברור

SMS אישור עסקה (דוגמאות) → תמיד "add_expense":
- "אישור עסקה: חויבת ב-X בסך ₪Y"
- "תודה על קנייתך ב-X! סכום: ₪Y"
- "התשלום עבור X בסך ₪Y בוצע בהצלחה"
- "Max: אישור עסקה ב-X על סך Y ₪"
- "שלום, בכרטיסך [ספרות] אושרה עסקה ב-[DD/MM] בסך [Y] ש"ח ב[X]" — ישראכרט
- "בכרטיס מסטרקארד שמסתיים ב[ספרות] קיבלנו בתאריך [DD/MM] בקשה לעסקה בחו"ל ב[X] בסך [Y] שח" — כאל """


# ── Public API ─────────────────────────────────────────────────────────────────

def parse_intent(user_message: str) -> str:
    """
    Classify a short user message into one of VALID_ACTIONS.
    Returns action string, defaults to 'unknown'.
    """
    prompt = f"{_INTENT_SYSTEM}\n\nהמשתמש אמר: {user_message}"
    raw = _call_ollama(prompt, num_predict=80)
    if not raw:
        return "unknown"
    data = _extract_json(raw)
    if not data:
        return "unknown"
    action = data.get("action", "unknown")
    from config import VALID_ACTIONS
    return action if action in VALID_ACTIONS else "unknown"


def parse_single_expense(user_message: str, known_merchants: dict[str, str]) -> dict | None:
    """
    Parse one expense from free-form text.
    known_merchants: {merchant_name: category} — hint to LLM for category.
    Returns {merchant, amount, currency, date, category} or None on failure.
    """
    hint = ""
    if known_merchants:
        hint = "\n\nבתי עסק מוכרים וקטגוריות שלהם (השתמש בהם אם מוזכרים):\n"
        hint += "\n".join(f'- "{m}": "{c}"' for m, c in list(known_merchants.items())[:20])

    prompt = f"{_SINGLE_SYSTEM}{hint}\n\nהמשתמש אמר: {user_message}"
    raw = _call_ollama(prompt, num_predict=200)
    if not raw:
        return None
    data = _extract_json(raw)
    if not data or data.get("action") != "add_expense":
        return None
    return _validate_single(data)


def parse_statement(text: str, known_merchants: dict[str, str]) -> list[dict]:
    """
    Parse a pasted credit card statement.
    Returns list of {merchant, amount, currency, date} dicts (no category yet —
    caller resolves categories via known_merchants + LLM suggestion).
    """
    hint = ""
    if known_merchants:
        hint = "\n\nבתי עסק מוכרים (כבר יש להם קטגוריה בבסיס הנתונים):\n"
        hint += ", ".join(f'"{m}"' for m in list(known_merchants.keys())[:30])

    prompt = f"{_STATEMENT_SYSTEM}{hint}\n\n--- תחילת החשבון ---\n{text}\n--- סוף החשבון ---"
    raw = _call_ollama(prompt, num_predict=4000)
    if not raw:
        return []
    data = _extract_json(raw)
    if not data or data.get("action") != "import_statement":
        return []
    raw_list = data.get("expenses", [])
    if not isinstance(raw_list, list):
        return []
    return [e for e in (_validate_single(e) for e in raw_list) if e]


def suggest_category(merchant_name: str) -> str:
    """
    Ask the LLM to suggest a category for an unknown merchant.
    Returns a category from CATEGORIES, or DEFAULT_CATEGORY on failure.
    """
    cat_list = ", ".join(f'"{c}"' for c in CATEGORIES)
    prompt = (
        f"אתה עוזר לסיווג הוצאות. החזר JSON בלבד.\n"
        f"קטגוריות: [{cat_list}]\n"
        f"{{\"category\":\"<קטגוריה מהרשימה>\"}}\n\n"
        f"בית עסק: \"{merchant_name}\""
    )
    raw = _call_ollama(prompt, num_predict=60)
    if not raw:
        return DEFAULT_CATEGORY
    data = _extract_json(raw)
    if not data:
        return DEFAULT_CATEGORY
    cat = data.get("category", DEFAULT_CATEGORY)
    return cat if cat in CATEGORIES else DEFAULT_CATEGORY


# ── Internal helpers ───────────────────────────────────────────────────────────

def _call_ollama(prompt: str, num_predict: int = 200) -> str | None:
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": num_predict},
            },
            timeout=60,
        )
        resp.raise_for_status()
        return resp.json().get("response", "").strip()
    except requests.RequestException as e:
        print(f"[parser] Ollama error: {e}")
        return None


def _extract_json(text: str) -> dict | None:
    """Strip markdown fences and extract first JSON object."""
    clean = text
    if "```" in clean:
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else clean
        clean = re.sub(r"^json\s*", "", clean, flags=re.I)
    clean = clean.strip()
    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start == -1 or end == 0:
        print(f"[parser] No JSON object found in: {repr(text[:120])}")
        return None
    try:
        return json.loads(clean[start:end])
    except json.JSONDecodeError as e:
        print(f"[parser] JSON decode error: {e} | snippet: {repr(clean[start:start+80])}")
        return None


def _validate_single(data: dict) -> dict | None:
    """Validate and normalise a single expense dict from the LLM."""
    merchant = str(data.get("merchant") or "").strip()
    if not merchant:
        return None

    try:
        amount = float(data.get("amount", 0))
    except (TypeError, ValueError):
        return None
    if amount <= 0:
        return None

    currency = str(data.get("currency") or "ILS").upper().strip()
    if currency not in {"ILS", "USD", "EUR", "GBP"}:
        currency = "ILS"

    raw_date = data.get("date")
    expense_date = _parse_date(raw_date)

    category = str(data.get("category") or DEFAULT_CATEGORY).strip()
    if category not in CATEGORIES:
        category = DEFAULT_CATEGORY

    return {
        "merchant": merchant,
        "amount": amount,
        "currency": currency,
        "date": expense_date,
        "category": category,
    }


def _parse_date(raw) -> str | None:
    """Try to parse various date formats into YYYY-MM-DD. Returns None on failure."""
    if not raw or raw == "null":
        return None
    s = str(raw).strip()
    # Already ISO
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    # DD/MM/YYYY or DD.MM.YYYY
    m = re.match(r"^(\d{1,2})[./](\d{1,2})[./](\d{2,4})$", s)
    if m:
        d, mo, y = m.groups()
        y = f"20{y}" if len(y) == 2 else y
        try:
            return date(int(y), int(mo), int(d)).isoformat()
        except ValueError:
            pass
    # DD/MM (year assumed current)
    m = re.match(r"^(\d{1,2})[./](\d{1,2})$", s)
    if m:
        d, mo = m.groups()
        try:
            return date(date.today().year, int(mo), int(d)).isoformat()
        except ValueError:
            pass
    return None
