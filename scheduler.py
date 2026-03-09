# scheduler.py — daily prediction reminders
#
# Sends a Telegram message with:
#   1. Items currently low (status = 'נמוך')
#   2. Items predicted to run out soon based on usage history
#
# Usage:
#   python scheduler.py
#
# Recommended cron (every day at 08:00):
#   0 8 * * * /home/aviad/shopping_bot/.venv/bin/python /home/aviad/shopping_bot/scheduler.py

import sqlite3
import sys
from datetime import datetime, timedelta

import requests

import db as db_module
from config import (
    CHAT_ID,
    DB_PATH,
    PREDICTION_THRESHOLD_DAYS,
    PREDICTION_WINDOW_DAYS,
    TELEGRAM_TOKEN,
)


def _get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def get_low_items() -> list[str]:
    """Items currently marked as 'נמוך' in inventory."""
    conn = _get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT i.name
        FROM inventory inv
        JOIN items i ON i.id = inv.item_id
        WHERE inv.status = 'נמוך'
        ORDER BY i.name
    """)
    result = [row["name"] for row in c.fetchall()]
    conn.close()
    return result


def get_predicted_items() -> list[tuple[str, int]]:
    """
    Items currently 'יש' that are predicted to run out within PREDICTION_THRESHOLD_DAYS.
    Returns list of (item_name, days_until_depletion).
    """
    cutoff = datetime.now() - timedelta(days=PREDICTION_WINDOW_DAYS)
    conn = _get_conn()
    c = conn.cursor()

    # Find items with at least 2 depletion events in the window
    c.execute("""
        SELECT
            ul.item_id,
            i.name,
            COUNT(*) AS depletions,
            MIN(ul.logged_at) AS first_log,
            MAX(ul.logged_at) AS last_log,
            inv.status,
            inv.last_updated
        FROM usage_log ul
        JOIN items i ON i.id = ul.item_id
        LEFT JOIN inventory inv ON inv.item_id = ul.item_id
        WHERE ul.change_type IN ('אין', 'נמוך')
          AND ul.logged_at > ?
        GROUP BY ul.item_id
        HAVING depletions >= 2 AND inv.status = 'יש'
    """, (cutoff.isoformat(),))
    rows = c.fetchall()
    conn.close()

    predicted = []
    for row in rows:
        try:
            first = datetime.fromisoformat(row["first_log"])
            last = datetime.fromisoformat(row["last_log"])
            last_updated = datetime.fromisoformat(row["last_updated"])
        except (TypeError, ValueError):
            continue

        depletions = row["depletions"]
        avg_cycle_days = (last - first).total_seconds() / 86400 / (depletions - 1)
        days_since_restock = (datetime.now() - last_updated).total_seconds() / 86400
        days_left = avg_cycle_days - days_since_restock

        if 0 < days_left <= PREDICTION_THRESHOLD_DAYS:
            predicted.append((row["name"], max(0, round(days_left))))

    return predicted


def get_expiring_items() -> list[dict]:
    """Items expiring within the next 30 days. Returns list of dicts with name, exp_date, days_left."""
    return db_module.get_expiring_soon_items(days=30)


def add_expiring_to_shopping_list(expiring: list[dict]) -> list[dict]:
    """Add expiring items to the shopping list with a note. Returns only newly added items."""
    newly_added = []
    for item in expiring:
        added = db_module.add_to_shopping_list(item["name"], note="פג תוקף בקרוב")
        if added:
            newly_added.append(item)
    return newly_added


def build_message(
    low: list[str],
    predicted: list[tuple[str, int]],
    expiring: list[dict] = None,
    newly_added_expiring: list[dict] = None,
) -> str | None:
    """Build Hebrew reminder message. Returns None if nothing to report."""
    expiring = expiring or []
    newly_added_expiring = newly_added_expiring or []

    if not low and not predicted and not expiring:
        return None

    lines = ["📦 תזכורת יומית — מה כדאי לקנות היום:\n"]

    if low:
        lines.append("⚠️ *נמוך במלאי:*")
        for name in low:
            lines.append(f"  • {name}")

    if predicted:
        if low:
            lines.append("")
        lines.append("🔮 *צפוי להיגמר בקרוב:*")
        for name, days in predicted:
            suffix = "היום" if days == 0 else f"בעוד {days} יום"
            lines.append(f"  • {name} ({suffix})")

    if expiring:
        if low or predicted:
            lines.append("")
        lines.append("📅 *פג תוקף בקרוב:*")
        for item in expiring:
            days_left = item["days_left"]
            suffix = "היום" if days_left == 0 else f"בעוד {days_left} יום"
            loc_tag = f" | 📍 {item['location']}" if item.get("location") and item["location"] != "בית" else ""
            lines.append(f"  • {item['name']} ({item['exp_date']} — {suffix}{loc_tag})")

    if newly_added_expiring:
        lines.append("")
        names = ", ".join(i["name"] for i in newly_added_expiring)
        lines.append(f"🛒 *הוספתי לרשימת הקניות:* {names}")

    lines.append("\n/list — לרשימת הקניות המלאה")
    return "\n".join(lines)


def send_telegram(token: str, chat_id: int, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    resp = requests.post(url, json={
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }, timeout=15)
    resp.raise_for_status()


def main():
    if TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print("[scheduler] TELEGRAM_TOKEN not set in config.py", file=sys.stderr)
        sys.exit(1)

    if not CHAT_ID:
        print("[scheduler] CHAT_ID not set in config.py", file=sys.stderr)
        sys.exit(1)

    db_module.init_db()

    low = get_low_items()
    predicted = get_predicted_items()
    expiring = get_expiring_items()
    newly_added = add_expiring_to_shopping_list(expiring)

    message = build_message(low, predicted, expiring, newly_added)

    if not message:
        print("[scheduler] Nothing to report today.")
        return

    send_telegram(TELEGRAM_TOKEN, CHAT_ID, message)
    print(
        f"[scheduler] Sent reminder: {len(low)} low, {len(predicted)} predicted, "
        f"{len(expiring)} expiring ({len(newly_added)} newly added to list)."
    )


if __name__ == "__main__":
    main()
