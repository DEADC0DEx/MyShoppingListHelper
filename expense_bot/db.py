# expense_bot/db.py — all database interactions

import os
import sqlite3
from datetime import datetime
from config import DB_PATH, DEFAULT_CATEGORY


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db():
    """Create all tables if they don't exist. Safe to call on every startup."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = get_conn()
    conn.executescript("""
        -- Normalized merchant registry with remembered category
        CREATE TABLE IF NOT EXISTS merchants (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            name     TEXT NOT NULL UNIQUE,   -- lowercased/stripped for matching
            display  TEXT NOT NULL,          -- original casing shown to user
            category TEXT NOT NULL
        );

        -- Every recorded expense
        CREATE TABLE IF NOT EXISTS expenses (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            merchant_id  INTEGER REFERENCES merchants(id),
            raw_merchant TEXT NOT NULL,     -- exactly as received (before normalization)
            amount       REAL NOT NULL,
            currency     TEXT NOT NULL DEFAULT 'ILS',
            expense_date TEXT,              -- YYYY-MM-DD, NULL if unknown
            category     TEXT NOT NULL,
            note         TEXT,
            source       TEXT NOT NULL DEFAULT 'manual',  -- 'manual'|'statement'|'csv'
            logged_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        -- Index for monthly queries
        CREATE INDEX IF NOT EXISTS idx_expenses_date ON expenses(expense_date);
        CREATE INDEX IF NOT EXISTS idx_expenses_category ON expenses(category);
    """)
    conn.commit()
    conn.close()


# ── Merchant helpers ───────────────────────────────────────────────────────────

def _normalize(name: str) -> str:
    return name.strip().lower()


def get_merchant_category(name: str) -> str | None:
    """Return stored category for this merchant, or None if unknown."""
    conn = get_conn()
    row = conn.execute(
        "SELECT category FROM merchants WHERE name = ?", (_normalize(name),)
    ).fetchone()
    conn.close()
    return row["category"] if row else None


def upsert_merchant(name: str, category: str) -> int:
    """Create or update a merchant's category. Returns merchant id."""
    conn = get_conn()
    key = _normalize(name)
    conn.execute("""
        INSERT INTO merchants (name, display, category) VALUES (?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET category = excluded.category,
                                        display  = excluded.display
    """, (key, name.strip(), category))
    conn.commit()
    row = conn.execute("SELECT id FROM merchants WHERE name = ?", (key,)).fetchone()
    merchant_id = row["id"]
    conn.close()
    return merchant_id


def get_all_merchants() -> list[dict]:
    """Return all merchants with their categories, alphabetically."""
    conn = get_conn()
    rows = conn.execute(
        "SELECT display, category FROM merchants ORDER BY display ASC"
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Expense helpers ────────────────────────────────────────────────────────────

def add_expense(
    raw_merchant: str,
    amount: float,
    category: str,
    expense_date: str | None = None,
    currency: str = "ILS",
    note: str | None = None,
    source: str = "manual",
) -> int:
    """
    Record one expense. Also upserts the merchant (keeping category).
    Returns new expense id.
    """
    merchant_id = upsert_merchant(raw_merchant, category)
    conn = get_conn()
    cursor = conn.execute("""
        INSERT INTO expenses
            (merchant_id, raw_merchant, amount, currency, expense_date, category, note, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (merchant_id, raw_merchant.strip(), amount, currency,
          expense_date, category, note, source))
    conn.commit()
    expense_id = cursor.lastrowid
    conn.close()
    return expense_id


def add_expenses_batch(expenses: list[dict], source: str = "statement") -> int:
    """
    Insert a list of expense dicts. Each dict must have:
      raw_merchant, amount, category
    Optional: expense_date, currency, note
    Returns count of inserted rows.
    """
    count = 0
    for exp in expenses:
        add_expense(
            raw_merchant=exp["raw_merchant"],
            amount=exp["amount"],
            category=exp["category"],
            expense_date=exp.get("expense_date"),
            currency=exp.get("currency", "ILS"),
            note=exp.get("note"),
            source=source,
        )
        count += 1
    return count


def get_expenses(year: int, month: int) -> list[dict]:
    """Return all expenses for a given year/month, newest first."""
    month_str = f"{year}-{month:02d}"
    conn = get_conn()
    rows = conn.execute("""
        SELECT e.id, e.raw_merchant, e.amount, e.currency,
               e.expense_date, e.category, e.note, e.source
        FROM expenses e
        WHERE strftime('%Y-%m', e.expense_date) = ?
           OR (e.expense_date IS NULL AND strftime('%Y-%m', e.logged_at) = ?)
        ORDER BY e.expense_date DESC, e.logged_at DESC
    """, (month_str, month_str)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_summary(year: int, month: int) -> dict[str, float]:
    """Return {category: total_amount} for the given month."""
    month_str = f"{year}-{month:02d}"
    conn = get_conn()
    rows = conn.execute("""
        SELECT category, SUM(amount) as total
        FROM expenses
        WHERE (strftime('%Y-%m', expense_date) = ?
               OR (expense_date IS NULL AND strftime('%Y-%m', logged_at) = ?))
          AND currency = 'ILS'
        GROUP BY category
        ORDER BY total DESC
    """, (month_str, month_str)).fetchall()
    conn.close()
    return {r["category"]: r["total"] for r in rows}


def update_expense_category(expense_id: int, category: str):
    conn = get_conn()
    conn.execute(
        "UPDATE expenses SET category = ? WHERE id = ?", (category, expense_id)
    )
    conn.commit()
    conn.close()


def find_duplicate(merchant: str, amount: float, expense_date: str | None) -> int | None:
    """
    Return the ID of an existing expense that likely matches, else None.
    Matches: same normalized merchant + same amount (±0.01) + date within 7 days.
    Falls back to merchant+amount only when no date is given.
    """
    from datetime import date as _date, timedelta
    conn = get_conn()
    key = _normalize(merchant)
    row = None
    if expense_date:
        try:
            d = _date.fromisoformat(expense_date)
            date_from = (d - timedelta(days=7)).isoformat()
            date_to   = (d + timedelta(days=7)).isoformat()
            row = conn.execute("""
                SELECT e.id FROM expenses e
                JOIN merchants m ON e.merchant_id = m.id
                WHERE m.name = ? AND ABS(e.amount - ?) < 0.01
                  AND e.expense_date BETWEEN ? AND ?
                LIMIT 1
            """, (key, amount, date_from, date_to)).fetchone()
        except ValueError:
            pass
    if row is None:
        row = conn.execute("""
            SELECT e.id FROM expenses e
            JOIN merchants m ON e.merchant_id = m.id
            WHERE m.name = ? AND ABS(e.amount - ?) < 0.01
            LIMIT 1
        """, (key, amount)).fetchone()
    conn.close()
    return row["id"] if row else None
