# list_flow.py — state machine for named shopping lists
#
# States:
#   saving    → waiting for user to send items (one per line) for a new list
#   selecting → paginating through groups of items, collecting selection

import db

GROUP_SIZE = 10

# { chat_id: {"state": str, "list_name": str, "items": list[str],
#             "offset": int, "added": list[str]} }
_sessions: dict[int, dict] = {}


def is_active(chat_id: int) -> bool:
    return chat_id in _sessions


def cancel(chat_id: int):
    _sessions.pop(chat_id, None)


def start_save(chat_id: int, list_name: str) -> str:
    _sessions[chat_id] = {"state": "saving", "list_name": list_name}
    return (
        f"שלח את פריטי הרשימה *{list_name}*.\n"
        "כל פריט בשורה נפרדת. שלח /cancel לביטול."
    )


def start_use(chat_id: int, list_name: str) -> str:
    items = db.get_named_list_items(list_name)
    if not items:
        return f"לא מצאתי רשימה בשם '{list_name}'."
    _sessions[chat_id] = {
        "state": "selecting",
        "list_name": list_name,
        "items": items,
        "offset": 0,
        "added": [],
    }
    return _group_prompt(_sessions[chat_id])


def _group_prompt(session: dict) -> str:
    items = session["items"]
    offset = session["offset"]
    group = items[offset: offset + GROUP_SIZE]
    total = len(items)
    end = min(offset + GROUP_SIZE, total)

    lines = [
        f"📋 *{session['list_name']}* — פריטים {offset + 1}–{end} מתוך {total}:\n"
    ]
    for i, item in enumerate(group, start=offset + 1):
        lines.append(f"  {i}. {item}")
    lines.append(
        "\nשלח מספרים מופרדים בפסיקים, *הכל* להוספת כולם, או *דלג* לקבוצה הבאה."
    )
    return "\n".join(lines)


def handle(chat_id: int, text: str) -> str:
    session = _sessions.get(chat_id)
    if not session:
        return ""

    if session["state"] == "saving":
        items = [
            line.strip()
            for line in text.splitlines()
            if line.strip() and not line.strip().startswith("*")
        ]
        if not items:
            return "לא קיבלתי פריטים. שלח כל פריט בשורה נפרדת, או /cancel לביטול."
        db.save_named_list(session["list_name"], items)
        del _sessions[chat_id]
        return f"✅ הרשימה *{session['list_name']}* נשמרה עם {len(items)} פריטים."

    if session["state"] == "selecting":
        all_items = session["items"]
        offset = session["offset"]
        group = all_items[offset: offset + GROUP_SIZE]
        t = text.strip()

        if t != "דלג":
            if t == "הכל":
                selected = group
            else:
                try:
                    # Numbers are global (1-based across full list)
                    indices = [int(n.strip()) - 1 for n in t.replace(" ", "").split(",")]
                    selected = [all_items[i] for i in indices if offset <= i < offset + GROUP_SIZE]
                except ValueError:
                    return "שלח מספרים מופרדים בפסיקים, *הכל*, או *דלג*."
            session["added"].extend(selected)

        # Advance to next group
        session["offset"] = offset + GROUP_SIZE

        if session["offset"] < len(all_items):
            return _group_prompt(session)

        # All groups done — commit and summarize
        added = session["added"]
        del _sessions[chat_id]
        if not added:
            return "לא הוספתי פריטים לרשימה."
        for item in added:
            db.add_to_shopping_list(item)
        lines = "\n".join(f"  • {item}" for item in added)
        return f"✅ הוספתי {len(added)} פריטים לרשימת הקניות:\n{lines}"

    return ""
