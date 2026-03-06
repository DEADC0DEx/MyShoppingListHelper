# executor.py — applies parsed actions to the database, builds Hebrew replies
#
# No LLM here. Pure deterministic logic.
# Input:  {"action": "...", "items": [...]}
# Output: Hebrew string to send back to the user

import db


def handle(parsed: dict) -> str:
    """
    Main entry point. Takes a validated parsed intent and returns a Hebrew reply.
    """
    action = parsed.get("action", "unknown")
    items = parsed.get("items", [])

    handlers = {
        "add":       _handle_add,
        "depleted":  _handle_depleted,
        "low":       _handle_low,
        "remove":    _handle_remove,
        "list":      _handle_list,
        "inventory": _handle_inventory,
        "recipe":    _handle_recipe,
        "unknown":   _handle_unknown,
    }

    handler = handlers.get(action, _handle_unknown)
    return handler(items)


# ── Action handlers ───────────────────────────────────────────────────────────

def _handle_add(items: list) -> str:
    if not items:
        return "לא הבנתי אילו פריטים להוסיף. אפשר לנסח מחדש?"

    added = []
    for item in items:
        db.set_inventory_status(item, "יש")
        db.remove_from_shopping_list(item)  # bought it — remove from list
        added.append(item)

    lines = "\n".join(f"  • {item}" for item in added)
    return f"✅ עודכן המלאי:\n{lines}"


def _handle_depleted(items: list) -> str:
    if not items:
        return "לא הבנתי מה נגמר. אפשר לנסח מחדש?"

    added_to_list = []
    for item in items:
        db.set_inventory_status(item, "אין")
        db.add_to_shopping_list(item)
        added_to_list.append(item)

    lines = "\n".join(f"  • {item}" for item in added_to_list)
    return f"📋 הוספתי לרשימת הקניות:\n{lines}"


def _handle_low(items: list) -> str:
    if not items:
        return "לא הבנתי אילו פריטים נמוכים. אפשר לנסח מחדש?"

    updated = []
    for item in items:
        db.set_inventory_status(item, "נמוך")
        db.add_to_shopping_list(item)
        updated.append(item)

    lines = "\n".join(f"  • {item}" for item in updated)
    return f"⚠️ סומן כנמוך והוספתי לרשימה:\n{lines}"


def _handle_remove(items: list) -> str:
    if not items:
        return "לא הבנתי מה להסיר. אפשר לנסח מחדש?"

    removed = []
    for item in items:
        db.remove_from_shopping_list(item)
        removed.append(item)

    lines = "\n".join(f"  • {item}" for item in removed)
    return f"🗑️ הוסר מהרשימה:\n{lines}"


def _handle_list(items: list) -> str:
    shopping = db.get_shopping_list()

    if not shopping:
        return "✨ רשימת הקניות ריקה! אין מה לקנות כרגע."

    lines = "\n".join(f"  • {row['name']}" for row in shopping)
    count = len(shopping)
    return f"🛒 רשימת הקניות ({count} פריטים):\n{lines}"


def _handle_inventory(items: list) -> str:
    inventory = db.get_inventory()

    if not inventory:
        return "המלאי ריק. תוסיף פריטים עם 'קניתי...' או 'יש בבית...'"

    # Group by status
    groups = {"יש": [], "נמוך": [], "אין": []}
    for row in inventory:
        status = row["status"]
        if status in groups:
            groups[status].append(row["name"])

    lines = []
    if groups["יש"]:
        lines.append("✅ יש:")
        lines += [f"  • {name}" for name in groups["יש"]]
    if groups["נמוך"]:
        lines.append("⚠️ נמוך:")
        lines += [f"  • {name}" for name in groups["נמוך"]]
    if groups["אין"]:
        lines.append("❌ אין:")
        lines += [f"  • {name}" for name in groups["אין"]]

    return "\n".join(lines)


def _handle_recipe(items: list) -> str:
    recipes = db.get_available_recipes()

    if not recipes:
        return (
            "👨‍🍳 לא מצאתי מתכונים שאפשר להכין עם מה שיש עכשיו.\n"
            "אפשר להוסיף מתכונים עם הפקודה /add_recipe"
        )

    lines = ["👨‍🍳 אפשר להכין עכשיו:"]
    for recipe in recipes:
        lines.append(f"  • {recipe['name']} ({recipe['servings']} מנות)")

    lines.append("\nתרצה פרטים על מתכון מסוים?")
    return "\n".join(lines)


def _handle_unknown(items: list) -> str:
    return (
        "לא הבנתי את הבקשה 🤔\n\n"
        "אפשר לנסות:\n"
        "  • *קניתי חלב וביצים* — לעדכן מלאי\n"
        "  • *נגמר הלחם* — לסמן שנגמר\n"
        "  • *מה צריך לקנות?* — לראות הרשימה\n"
        "  • *מה יש בבית?* — לראות המלאי\n"
        "  • *מה אפשר לבשל?* — הצעות למנות"
    )
