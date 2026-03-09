# receipt_flow.py — stateful conversation for confirming uncertain product names
#
# After a receipt import, some raw product names can't be normalized confidently
# by the LLM.  This module asks the user about them one-by-one and saves the
# confirmed translations to the CSV cache so future imports don't need to ask.
#
# Usage in bot.py:
#   if receipt_flow.is_active(chat_id):
#       reply = receipt_flow.handle(chat_id, user_text)
#   ...
#   reply = receipt_flow.start(chat_id, uncertain_raws)

import db
import receipt_parser

# chat_id → {"queue": [raw_str, ...], "done": int}
_state: dict = {}


def start(chat_id: int, uncertain_raws: list) -> str:
    """
    Begin the confirmation conversation for a list of uncertain raw product names.
    Returns the first question to send to the user.
    Call only when len(uncertain_raws) > 0.
    """
    _state[chat_id] = {"queue": list(uncertain_raws), "done": 0}
    return _build_question(chat_id)


def is_active(chat_id: int) -> bool:
    return chat_id in _state


def handle(chat_id: int, user_text: str) -> str:
    """
    Process the user's reply for the current item.

    - Any non-empty text is treated as the correct product name.
    - "דלג" or "skip" skips the current item without saving.
    Returns the next question, or a completion summary when the queue is empty.
    """
    if chat_id not in _state:
        return ""

    state = _state[chat_id]
    raw = state["queue"][0]
    text = user_text.strip()

    if text.lower() in ("דלג", "skip", "/skip"):
        # Skip without saving
        state["queue"].pop(0)
        print(f"[receipt_flow] Skipped '{raw}'")
    elif text:
        # Save translation, update inventory, remove from shopping list
        receipt_parser.save_translation(raw, text)
        db.set_inventory_status(text, "יש")
        db.remove_from_shopping_list(text)
        state["done"] += 1
        state["queue"].pop(0)
        print(f"[receipt_flow] Confirmed '{raw}' → '{text}'")

    if state["queue"]:
        return _build_question(chat_id)

    # All done
    done = state["done"]
    del _state[chat_id]
    if done:
        return f"✅ תודה! הוספתי {done} פריטים נוספים למלאי ושמרתי את התרגומים לשימוש עתידי."
    return "✅ סיימנו."


def cancel(chat_id: int) -> None:
    _state.pop(chat_id, None)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_question(chat_id: int) -> str:
    state = _state[chat_id]
    raw = state["queue"][0]
    remaining = len(state["queue"])
    return (
        f"לא הצלחתי לזהות את המוצר הזה:\n"
        f"  `{raw}`\n\n"
        f"מה שמו הנכון בעברית?\n"
        f"_(כתוב את השם, או *דלג* לדלג | /cancel לביטול)_\n"
        f"נותרו {remaining} מוצרים לאישור."
    )
