# db.py — all database interactions

import os
import sqlite3
from datetime import datetime
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # allows dict-like access to rows
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    c = conn.cursor()

    c.executescript("""
        CREATE TABLE IF NOT EXISTS items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            category TEXT DEFAULT 'כללי',
            default_unit TEXT DEFAULT 'יחידות'
        );

        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            status TEXT NOT NULL DEFAULT 'יש' CHECK(status IN ('יש', 'נמוך', 'אין')),
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            exp_date TEXT DEFAULT NULL,
            location TEXT NOT NULL DEFAULT 'בית'
        );

        CREATE TABLE IF NOT EXISTS shopping_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bought INTEGER DEFAULT 0,
            note TEXT DEFAULT NULL
        );

        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            change_type TEXT NOT NULL,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            instructions TEXT,
            servings INTEGER DEFAULT 4
        );

        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            recipe_id INTEGER REFERENCES recipes(id),
            item_id INTEGER REFERENCES items(id),
            quantity REAL,
            unit TEXT
        );

        CREATE TABLE IF NOT EXISTS failed_parses (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_message TEXT NOT NULL,
            raw_llm_response TEXT NOT NULL,
            logged_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS named_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS named_list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            list_id INTEGER NOT NULL REFERENCES named_lists(id) ON DELETE CASCADE,
            item_name TEXT NOT NULL,
            position INTEGER NOT NULL
        );
    """)

    conn.commit()

    # Migrate existing databases: add new columns if they don't exist yet
    for migration in [
        "ALTER TABLE inventory ADD COLUMN exp_date TEXT DEFAULT NULL",
        "ALTER TABLE inventory ADD COLUMN location TEXT NOT NULL DEFAULT 'בית'",
        "ALTER TABLE shopping_list ADD COLUMN note TEXT DEFAULT NULL",
    ]:
        try:
            c.execute(migration)
            conn.commit()
        except sqlite3.OperationalError:
            pass  # Column already exists

    conn.close()


# ── Item helpers ──────────────────────────────────────────────────────────────

def get_or_create_item(name: str) -> int:
    """Return item id, creating the item if it doesn't exist."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM items WHERE name = ?", (name,))
    row = c.fetchone()

    if row:
        item_id = row["id"]
    else:
        c.execute("INSERT INTO items (name) VALUES (?)", (name,))
        item_id = c.lastrowid
        conn.commit()

    conn.close()
    return item_id


# ── Inventory ─────────────────────────────────────────────────────────────────

def set_inventory_status(item_name: str, status: str, location: str = "בית"):
    """Set an item's inventory status for a given location. Creates records if needed."""
    item_id = get_or_create_item(item_name)
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM inventory WHERE item_id = ? AND location = ?", (item_id, location))
    row = c.fetchone()

    if row:
        c.execute(
            "UPDATE inventory SET status = ?, last_updated = ? WHERE item_id = ? AND location = ?",
            (status, datetime.now(), item_id, location)
        )
    else:
        c.execute(
            "INSERT INTO inventory (item_id, status, location) VALUES (?, ?, ?)",
            (item_id, status, location)
        )

    # Log the change
    c.execute(
        "INSERT INTO usage_log (item_id, change_type) VALUES (?, ?)",
        (item_id, status)
    )

    conn.commit()
    conn.close()


def get_inventory(location: str = None) -> list[dict]:
    """Return inventory items with their status.
    If location is given, return only items from that location.
    Otherwise return all items, including a 'location' field."""
    conn = get_conn()
    c = conn.cursor()
    if location:
        c.execute("""
            SELECT i.name, inv.status, inv.last_updated, inv.location, inv.exp_date
            FROM inventory inv
            JOIN items i ON i.id = inv.item_id
            WHERE inv.location = ?
            ORDER BY inv.status ASC, i.name ASC
        """, (location,))
    else:
        c.execute("""
            SELECT i.name, inv.status, inv.last_updated, inv.location, inv.exp_date
            FROM inventory inv
            JOIN items i ON i.id = inv.item_id
            ORDER BY inv.location ASC, inv.status ASC, i.name ASC
        """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def set_expiration_date(item_name: str, exp_date: str, location: str = "בית"):
    """Set or update the expiration date (YYYY-MM-DD) for an inventory item at a given location."""
    item_id = get_or_create_item(item_name)
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM inventory WHERE item_id = ? AND location = ?", (item_id, location))
    row = c.fetchone()
    if row:
        c.execute(
            "UPDATE inventory SET exp_date = ? WHERE item_id = ? AND location = ?",
            (exp_date, item_id, location)
        )
    else:
        c.execute(
            "INSERT INTO inventory (item_id, status, exp_date, location) VALUES (?, 'יש', ?, ?)",
            (item_id, exp_date, location)
        )

    conn.commit()
    conn.close()


def get_expiring_soon_items(days: int = 30, location: str = None) -> list[dict]:
    """Return inventory items whose exp_date falls within the next `days` days.
    Optionally filter by location. Each result has keys: name, exp_date, days_left, location."""
    conn = get_conn()
    c = conn.cursor()
    if location:
        c.execute("""
            SELECT i.name, inv.exp_date, inv.location
            FROM inventory inv
            JOIN items i ON i.id = inv.item_id
            WHERE inv.exp_date IS NOT NULL AND inv.location = ?
            ORDER BY inv.exp_date ASC
        """, (location,))
    else:
        c.execute("""
            SELECT i.name, inv.exp_date, inv.location
            FROM inventory inv
            JOIN items i ON i.id = inv.item_id
            WHERE inv.exp_date IS NOT NULL
            ORDER BY inv.exp_date ASC
        """)
    rows = c.fetchall()
    conn.close()

    today = datetime.now().date()
    result = []
    for row in rows:
        try:
            exp = datetime.strptime(row["exp_date"], "%Y-%m-%d").date()
        except (ValueError, TypeError):
            continue
        days_left = (exp - today).days
        if 0 <= days_left <= days:
            result.append({
                "name": row["name"],
                "exp_date": row["exp_date"],
                "days_left": days_left,
                "location": row["location"],
            })
    return result


# ── Shopping list ─────────────────────────────────────────────────────────────

def add_to_shopping_list(item_name: str, note: str = None) -> bool:
    """Add item to shopping list if not already there (pending). Returns True if newly added."""
    item_id = get_or_create_item(item_name)
    conn = get_conn()
    c = conn.cursor()

    # Don't add duplicates
    c.execute(
        "SELECT id FROM shopping_list WHERE item_id = ? AND bought = 0",
        (item_id,)
    )
    if not c.fetchone():
        c.execute(
            "INSERT INTO shopping_list (item_id, note) VALUES (?, ?)",
            (item_id, note)
        )
        conn.commit()
        conn.close()
        return True

    conn.close()
    return False


def remove_from_shopping_list(item_name: str):
    """Mark item as bought on the shopping list."""
    item_id = get_or_create_item(item_name)
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE shopping_list SET bought = 1 WHERE item_id = ? AND bought = 0",
        (item_id,)
    )
    conn.commit()
    conn.close()


def get_shopping_list() -> list[dict]:
    """Return all pending shopping list items."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT i.name, sl.added_at, sl.note
        FROM shopping_list sl
        JOIN items i ON i.id = sl.item_id
        WHERE sl.bought = 0
        ORDER BY sl.added_at ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def clear_shopping_list():
    """Mark all pending items as bought."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("UPDATE shopping_list SET bought = 1 WHERE bought = 0")
    conn.commit()
    conn.close()


# ── Recipes ───────────────────────────────────────────────────────────────────

def get_available_recipes() -> list[dict]:
    """Return recipes where all ingredients are currently 'יש' in inventory."""
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id, name, instructions, servings FROM recipes")
    recipes = [dict(r) for r in c.fetchall()]

    available = []
    for recipe in recipes:
        c.execute("""
            SELECT COUNT(*) as total FROM recipe_ingredients WHERE recipe_id = ?
        """, (recipe["id"],))
        total = c.fetchone()["total"]

        if total == 0:
            continue

        c.execute("""
            SELECT COUNT(*) as have FROM recipe_ingredients ri
            JOIN inventory inv ON inv.item_id = ri.item_id
            WHERE ri.recipe_id = ? AND inv.status = 'יש'
        """, (recipe["id"],))
        have = c.fetchone()["have"]

        if have == total:
            available.append(recipe)

    conn.close()
    return available


def get_all_recipes() -> list[dict]:
    """Return all recipes (id, name, servings) — no ingredients."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id, name, servings FROM recipes ORDER BY name ASC")
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_recipe_by_name(name: str) -> dict | None:
    """Return a recipe with its ingredients by name (case-insensitive partial match)."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, name, instructions, servings FROM recipes WHERE name LIKE ?",
        (f"%{name}%",)
    )
    row = c.fetchone()
    if not row:
        conn.close()
        return None

    recipe = dict(row)
    c.execute("""
        SELECT i.name as item, ri.quantity, ri.unit
        FROM recipe_ingredients ri
        JOIN items i ON i.id = ri.item_id
        WHERE ri.recipe_id = ?
    """, (recipe["id"],))
    recipe["ingredients"] = [dict(r) for r in c.fetchall()]
    conn.close()
    return recipe


def delete_recipe(name: str) -> bool:
    """Delete a recipe by name. Returns True if found and deleted."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM recipes WHERE name LIKE ?", (f"%{name}%",))
    row = c.fetchone()
    if not row:
        conn.close()
        return False
    recipe_id = row["id"]
    c.execute("DELETE FROM recipe_ingredients WHERE recipe_id = ?", (recipe_id,))
    c.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,))
    conn.commit()
    conn.close()
    return True


def add_recipe(name: str, instructions: str, ingredients: list[tuple], servings: int = 4) -> int:
    """
    Add a recipe with ingredients.
    ingredients: list of (item_name, quantity, unit) tuples
    """
    conn = get_conn()
    c = conn.cursor()

    c.execute(
        "INSERT INTO recipes (name, instructions, servings) VALUES (?, ?, ?)",
        (name, instructions, servings)
    )
    recipe_id = c.lastrowid

    for item_name, quantity, unit in ingredients:
        item_id = get_or_create_item(item_name)
        c.execute(
            "INSERT INTO recipe_ingredients (recipe_id, item_id, quantity, unit) VALUES (?, ?, ?, ?)",
            (recipe_id, item_id, quantity, unit)
        )

    conn.commit()
    conn.close()
    return recipe_id

# ── Named lists ───────────────────────────────────────────────────────────────

def save_named_list(name: str, items: list[str]):
    """Create or replace a named list with the given items."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO named_lists (name) VALUES (?) ON CONFLICT(name) DO UPDATE SET created_at = CURRENT_TIMESTAMP", (name,))
    list_id = c.execute("SELECT id FROM named_lists WHERE name = ?", (name,)).fetchone()["id"]
    c.execute("DELETE FROM named_list_items WHERE list_id = ?", (list_id,))
    for pos, item in enumerate(items):
        c.execute(
            "INSERT INTO named_list_items (list_id, item_name, position) VALUES (?, ?, ?)",
            (list_id, item, pos)
        )
    conn.commit()
    conn.close()


def get_all_named_lists() -> list[str]:
    """Return names of all saved lists."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT name FROM named_lists ORDER BY created_at ASC")
    names = [r["name"] for r in c.fetchall()]
    conn.close()
    return names


def get_named_list_items(name: str) -> list[str]:
    """Return item names for a saved list, in order. Empty list if not found."""
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT id FROM named_lists WHERE name = ?", (name,)).fetchone()
    if not row:
        conn.close()
        return []
    items = [
        r["item_name"]
        for r in c.execute(
            "SELECT item_name FROM named_list_items WHERE list_id = ? ORDER BY position ASC",
            (row["id"],)
        ).fetchall()
    ]
    conn.close()
    return items


def delete_named_list(name: str) -> bool:
    """Delete a named list and its items. Returns True if found."""
    conn = get_conn()
    c = conn.cursor()
    row = c.execute("SELECT id FROM named_lists WHERE name = ?", (name,)).fetchone()
    if not row:
        conn.close()
        return False
    c.execute("DELETE FROM named_list_items WHERE list_id = ?", (row["id"],))
    c.execute("DELETE FROM named_lists WHERE id = ?", (row["id"],))
    conn.commit()
    conn.close()
    return True


# ── Failed parses ─────────────────────────────────────────────────────────────

def log_failed_parse(user_message: str, raw_llm_response: str):
    """Log a message that the intent parser could not understand."""
    conn = get_conn()
    conn.execute(
        "INSERT INTO failed_parses (user_message, raw_llm_response) VALUES (?, ?)",
        (user_message, raw_llm_response)
    )
    conn.commit()
    conn.close()


def get_failed_parses(limit: int = 10) -> list[dict]:
    """Return the most recent failed parse entries."""
    conn = get_conn()
    c = conn.cursor()
    c.execute(
        "SELECT id, user_message, raw_llm_response, logged_at FROM failed_parses ORDER BY id DESC LIMIT ?",
        (limit,)
    )
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def get_recipe_missing_ingredients(recipe_id: int) -> list[str]:
    """Return ingredient names for a recipe that are NOT currently 'יש' in inventory."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT i.name
        FROM recipe_ingredients ri
        JOIN items i ON i.id = ri.item_id
        LEFT JOIN inventory inv ON inv.item_id = ri.item_id
        WHERE ri.recipe_id = ?
          AND (inv.status IS NULL OR inv.status != 'יש')
    """, (recipe_id,))
    missing = [row["name"] for row in c.fetchall()]
    conn.close()
    return missing
