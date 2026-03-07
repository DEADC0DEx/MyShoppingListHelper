# intent_parser.py — the ONLY place the LLM is called
#
# Critical design rules (learned from testing):
#   1. LLM never sees the current list — it only parses the user's message
#   2. Every response is validated against the schema in Python
#   3. Hebrew is forced explicitly in every prompt
#   4. On any failure, return {"action": "unknown", "items": []}

import json
import requests
import db
from config import OLLAMA_URL, OLLAMA_MODEL, VALID_ACTIONS

# ── Prompt ────────────────────────────────────────────────────────────────────
#
# DictaLM uses a standard chat format (not [INST] tags).
# The system prompt is embedded as a user-turn instruction.

SYSTEM_PROMPT = """אתה עוזר בית לניהול קניות. אתה מחזיר JSON בלבד.
אסור לכתוב טקסט נוסף, הסברים, או markdown.

הסכמה המדויקת — החזר אובייקט JSON אחד בלבד:
{"action": "<פעולה>", "items": ["<פריט1>", "<פריט2>"]}

פעולות מותרות:
- "add"       — המשתמש קנה או הוסיף פריטים למלאי
- "remove"    — המשתמש רוצה להסיר פריט מהרשימה
- "depleted"  — פריט נגמר לגמרי
- "low"       — פריט עומד להיגמר / נמוך
- "list"      — המשתמש רוצה לראות את רשימת הקניות
- "inventory" — המשתמש רוצה לראות מה יש בבית
- "recipe"    — המשתמש רוצה הצעות למנות או מתכונים
- "unknown"   — לא הובן הבקשה

חוקים חשובים:
- תענה בעברית בלבד בשדה items
- אל תמציא פריטים שלא הוזכרו בהודעה
- אם אין פריטים רלוונטיים, החזר items כמערך ריק: []
- action חייב להיות אחד מהערכים המותרים בלבד
- אל תוסיף שדות נוספים לJSON"""


def parse_intent(user_message: str) -> dict:
    """
    Send user message to LLM, return validated action dict.
    Always returns a dict with 'action' and 'items' keys.
    Never raises — returns {"action": "unknown", "items": []} on any failure.
    """
    prompt = f"{SYSTEM_PROMPT}\n\nהמשתמש אמר: {user_message}"

    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {
                    "temperature": 0.1,   # low temperature = more predictable JSON
                    "num_predict": 150,   # we only need a short JSON response
                }
            },
            timeout=30
        )
        response.raise_for_status()
        raw_text = response.json().get("response", "").strip()

    except requests.RequestException as e:
        print(f"[intent_parser] Ollama request failed: {e}")
        return {"action": "unknown", "items": []}

    result = _validate(raw_text, original_message=user_message)
    if result["action"] == "unknown":
        db.log_failed_parse(user_message, raw_text)
    return result


def _validate(raw_text: str, original_message: str) -> dict:
    """
    Parse and validate LLM output. 
    Returns safe default on any schema violation.
    """
    # Strip accidental markdown fences
    clean = raw_text
    if "```" in clean:
        clean = clean.split("```")[1] if "```" in clean else clean
        clean = clean.removeprefix("json").strip()
    clean = clean.strip()

    # Extract first JSON object if there's extra text
    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start == -1 or end == 0:
        print(f"[intent_parser] No JSON found in: {repr(raw_text)}")
        return {"action": "unknown", "items": []}

    json_str = clean[start:end]

    try:
        parsed = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"[intent_parser] JSON parse error: {e} | raw: {repr(json_str)}")
        return {"action": "unknown", "items": []}

    # Validate required fields
    if "action" not in parsed:
        print(f"[intent_parser] Missing 'action' field: {parsed}")
        return {"action": "unknown", "items": []}

    if parsed["action"] not in VALID_ACTIONS:
        print(f"[intent_parser] Invalid action '{parsed['action']}'")
        return {"action": "unknown", "items": []}

    if "items" not in parsed or not isinstance(parsed["items"], list):
        parsed["items"] = []

    # Sanitize items — strings only, strip whitespace
    parsed["items"] = [
        str(item).strip()
        for item in parsed["items"]
        if item and str(item).strip()
    ]

    print(f"[intent_parser] '{original_message}' → {parsed}")
    return parsed
