# recipe_flow.py — state machine for multi-turn recipe conversation
#
# States:
#   idle               → normal bot operation, recipe_flow ignores messages
#   awaiting_recipe    → waiting for user to send content (text/image/url)
#   awaiting_confirm   → recipe parsed, waiting for yes/no confirmation
#   awaiting_step_name → step-by-step: waiting for recipe name
#   awaiting_step_ingr → step-by-step: waiting for ingredients
#   awaiting_step_inst → step-by-step: waiting for instructions
#   awaiting_step_serv → step-by-step: waiting for servings count

import re
import db
import recipe_parser
import recipe_input

# In-memory state per chat_id.
# Single-household use — no persistence needed, state resets on bot restart.
# { chat_id: {"state": str, "data": dict} }
_sessions: dict[int, dict] = {}


# ── Session helpers ───────────────────────────────────────────────────────────

def get_state(chat_id: int) -> str:
    return _sessions.get(chat_id, {}).get("state", "idle")


def is_active(chat_id: int) -> bool:
    return get_state(chat_id) != "idle"


def _set(chat_id: int, state: str, data: dict = None):
    _sessions[chat_id] = {"state": state, "data": data or {}}


def _data(chat_id: int) -> dict:
    return _sessions.get(chat_id, {}).get("data", {})


def _clear(chat_id: int):
    _sessions.pop(chat_id, None)


# ── Public entry points ───────────────────────────────────────────────────────

def start(chat_id: int) -> str:
    """Called when the user expresses intent to add a recipe."""
    _set(chat_id, "awaiting_recipe")
    return (
        "בשמחה! שלח לי את המתכון באחת מהדרכים:\n\n"
        "📝 *טקסט חופשי* — שם המתכון, מרכיבים, הוראות\n"
        "🖼 *תמונה* — שלח תמונה של המתכון\n"
        "🔗 *קישור* — הדבק קישור לאתר מתכונים\n"
        "🪜 */step* — הוספה שלב-שלב\n\n"
        "לביטול: /cancel"
    )


def start_step_by_step(chat_id: int) -> str:
    """Begin guided step-by-step recipe entry."""
    _set(chat_id, "awaiting_step_name", {})
    return "בסדר, נעשה שלב-שלב 🙂\n\nמה *שם המתכון*?"


# ── Main dispatcher ───────────────────────────────────────────────────────────

def handle(chat_id: int, text: str = None, image_bytes: bytes = None) -> str | None:
    """
    Route incoming message to the right state handler.
    Returns reply string, or None if not currently in a recipe flow.
    """
    state = get_state(chat_id)

    if state == "idle":
        return None

    # /cancel works from any state
    if text and text.strip().lower() in {"/cancel", "ביטול"}:
        _clear(chat_id)
        return "בוטל ✅ חזרנו לפעולה רגילה."

    dispatch = {
        "awaiting_recipe":    _handle_awaiting_recipe,
        "awaiting_confirm":   _handle_confirm,
        "awaiting_step_name": _handle_step_name,
        "awaiting_step_ingr": _handle_step_ingredients,
        "awaiting_step_inst": _handle_step_instructions,
        "awaiting_step_serv": _handle_step_servings,
    }

    handler = dispatch.get(state)
    if handler:
        return handler(chat_id, text, image_bytes)

    _clear(chat_id)
    return None


# ── State: awaiting_recipe ────────────────────────────────────────────────────

def _handle_awaiting_recipe(chat_id: int, text: str, image_bytes: bytes) -> str:
    # Image
    if image_bytes:
        return _ocr_and_confirm(chat_id, image_bytes)

    if not text:
        return "לא קיבלתי כלום. שלח טקסט, תמונה, קישור, או /cancel לביטול."

    text = text.strip()

    # Step-by-step command
    if text.lower() in {"/step", "שלב שלב", "שלב-שלב"}:
        return start_step_by_step(chat_id)

    # URL
    if recipe_input.text_is_url(text):
        return _url_and_confirm(chat_id, text)

    # Free text
    return _text_and_confirm(chat_id, text)


def _text_and_confirm(chat_id: int, text: str) -> str:
    recipe = recipe_parser.parse_recipe_from_text(text)
    return _present_or_fail(chat_id, recipe)


def _url_and_confirm(chat_id: int, url: str) -> str:
    try:
        page_text = recipe_input.url_to_text(url)
    except RuntimeError as e:
        return f"❌ {e}\nנסה קישור אחר או /cancel לביטול."

    recipe = recipe_parser.parse_recipe_from_text(page_text)
    return _present_or_fail(chat_id, recipe)


def _ocr_and_confirm(chat_id: int, image_bytes: bytes) -> str:
    try:
        ocr_text = recipe_input.image_to_text(image_bytes)
    except RuntimeError as e:
        return f"❌ {e}"

    if not ocr_text:
        return (
            "לא הצלחתי לקרוא טקסט מהתמונה 😕\n"
            "נסה תמונה ברורה יותר, או /step להוספה ידנית."
        )

    recipe = recipe_parser.parse_recipe_from_text(ocr_text)
    return _present_or_fail(chat_id, recipe)


def _present_or_fail(chat_id: int, recipe: dict | None) -> str:
    if not recipe:
        return (
            "לא הצלחתי לחלץ מתכון 😕\n"
            "נסה ניסוח ברור יותר, קישור אחר, או /step להוספה ידנית.\n"
            "/cancel לביטול."
        )
    _set(chat_id, "awaiting_confirm", {"recipe": recipe})
    return recipe_parser.format_recipe_confirmation(recipe)


# ── State: awaiting_confirm ───────────────────────────────────────────────────

def _handle_confirm(chat_id: int, text: str, image_bytes: bytes) -> str:
    if not text:
        return "אנא ענה *כן* לשמירה או *לא* לביטול."

    text = text.strip().lower()
    yes = {"כן", "yes", "y", "אישור", "שמור", "ok", "אוקי", "בסדר", "✅"}
    no  = {"לא", "no", "n", "ביטול"}

    if text in yes:
        recipe = _data(chat_id).get("recipe")
        _clear(chat_id)
        if not recipe:
            return "משהו השתבש. נסה שוב."
        _save_recipe(recipe)
        return f"✅ המתכון *{recipe['name']}* נשמר!"

    if text in no:
        _clear(chat_id)
        return "המתכון לא נשמר. שלח מתכון חדש או /cancel."

    return "לא הבנתי. ענה *כן* לשמירה או *לא* לביטול."


# ── Step-by-step states ───────────────────────────────────────────────────────

def _handle_step_name(chat_id: int, text: str, image_bytes: bytes) -> str:
    if not text or not text.strip():
        return "צריך שם. מה *שם המתכון*?"
    name = text.strip()
    _set(chat_id, "awaiting_step_ingr", {"name": name})
    return (
        f"✅ *{name}*\n\n"
        "עכשיו שלח את *המרכיבים*, כל אחד בשורה:\n"
        "לדוגמה:\n"
        "3 ביצים\n"
        "2 כפות שמן זית\n"
        "קורט מלח"
    )


def _handle_step_ingredients(chat_id: int, text: str, image_bytes: bytes) -> str:
    if not text or not text.strip():
        return "שלח את המרכיבים, כל אחד בשורה נפרדת."
    data = _data(chat_id)
    data["ingredients_raw"] = text.strip()
    _set(chat_id, "awaiting_step_inst", data)
    return (
        "✅ קיבלתי את המרכיבים.\n\n"
        "שלח *הוראות הכנה* (אופציונלי), או כתוב *דלג*."
    )


def _handle_step_instructions(chat_id: int, text: str, image_bytes: bytes) -> str:
    data = _data(chat_id)
    skip = {"דלג", "skip", "-", "אין", "לא", ""}
    instructions = "" if (text or "").strip().lower() in skip else (text or "").strip()
    data["instructions"] = instructions
    _set(chat_id, "awaiting_step_serv", data)
    return "לכמה *מנות* המתכון? (מספר, או *דלג* לברירת מחדל 4)"


def _handle_step_servings(chat_id: int, text: str, image_bytes: bytes) -> str:
    data = _data(chat_id)
    skip = {"דלג", "skip", "-", ""}
    try:
        servings = 4 if (text or "").strip().lower() in skip else int(text.strip())
    except (ValueError, AttributeError):
        servings = 4

    # Ask LLM to normalize the ingredient lines
    raw = f"מתכון: {data['name']}\nמרכיבים:\n{data['ingredients_raw']}"
    recipe = recipe_parser.parse_recipe_from_text(raw)

    if recipe:
        recipe["servings"] = servings
        if data.get("instructions"):
            recipe["instructions"] = data["instructions"]
    else:
        # Fallback: parse ingredients without LLM
        recipe = {
            "name": data["name"],
            "servings": servings,
            "instructions": data.get("instructions", ""),
            "ingredients": _parse_ingredients_fallback(data["ingredients_raw"]),
        }

    _set(chat_id, "awaiting_confirm", {"recipe": recipe})
    return recipe_parser.format_recipe_confirmation(recipe)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _save_recipe(recipe: dict):
    ingredients = [
        (ing["item"], ing["quantity"], ing["unit"])
        for ing in recipe["ingredients"]
    ]
    db.add_recipe(
        name=recipe["name"],
        instructions=recipe.get("instructions", ""),
        ingredients=ingredients,
        servings=recipe.get("servings", 4),
    )


def _parse_ingredients_fallback(text: str) -> list[dict]:
    """Simple line-by-line ingredient parser — used if LLM call fails."""
    units = {
        "כף", "כפות", "כפית", "כפיות", "כוס", "כוסות",
        "גרם", "ק\"ג", "מ\"ל", "ליטר", "קורט",
        "חבילה", "פרוסה", "פרוסות", "ענף", "ענפים", "יחידות",
    }
    ingredients = []
    for line in text.strip().splitlines():
        line = line.strip().lstrip("•-–* ")
        if not line:
            continue
        m = re.match(r"^([\d/.]+)\s*(.*)", line)
        if m:
            try:
                qty = float(m.group(1))
            except ValueError:
                qty = 1.0
            rest = m.group(2).strip()
        else:
            qty, rest = 1.0, line

        words = rest.split()
        if words and words[0] in units:
            unit = words[0]
            item = " ".join(words[1:]) or unit
        else:
            unit, item = "יחידות", rest

        if item:
            ingredients.append({"item": item, "quantity": qty, "unit": unit})

    return ingredients
