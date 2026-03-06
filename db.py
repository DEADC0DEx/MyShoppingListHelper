# db.py — all database interactions

import sqlite3
from datetime import datetime
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row  # allows dict-like access to rows
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
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
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS shopping_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item_id INTEGER NOT NULL REFERENCES items(id),
            added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            bought INTEGER DEFAULT 0
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
    """)

    conn.commit()
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

def set_inventory_status(item_name: str, status: str):
    """Set an item's inventory status. Creates item and inventory record if needed."""
    item_id = get_or_create_item(item_name)
    conn = get_conn()
    c = conn.cursor()

    c.execute("SELECT id FROM inventory WHERE item_id = ?", (item_id,))
    row = c.fetchone()

    if row:
        c.execute(
            "UPDATE inventory SET status = ?, last_updated = ? WHERE item_id = ?",
            (status, datetime.now(), item_id)
        )
    else:
        c.execute(
            "INSERT INTO inventory (item_id, status) VALUES (?, ?)",
            (item_id, status)
        )

    # Log the change
    c.execute(
        "INSERT INTO usage_log (item_id, change_type) VALUES (?, ?)",
        (item_id, status)
    )

    conn.commit()
    conn.close()


def get_inventory() -> list[dict]:
    """Return all inventory items with their status."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT i.name, inv.status, inv.last_updated
        FROM inventory inv
        JOIN items i ON i.id = inv.item_id
        ORDER BY inv.status ASC, i.name ASC
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


# ── Shopping list ─────────────────────────────────────────────────────────────

def add_to_shopping_list(item_name: str):
    """Add item to shopping list if not already there (pending)."""
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
            "INSERT INTO shopping_list (item_id) VALUES (?)",
            (item_id,)
        )
        conn.commit()

    conn.close()


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
        SELECT i.name, sl.added_at
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
