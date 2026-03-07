# expense_bot/web_ui.py — local web interface for statement / CSV import
#
# Run:   python web_ui.py
# Open:  http://localhost:5000
#
# Single-user, local only. No authentication needed.

import csv
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from flask import Flask, request, redirect, url_for, render_template

import db
import parser as expense_parser
import executor
from config import CATEGORIES, DEFAULT_CATEGORY

app = Flask(__name__)

# Single-user in-process state — no cookie size limits, no serialization issues.
_pending: list[dict] = []


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("upload.html")


@app.route("/upload", methods=["POST"])
def upload():
    global _pending
    expenses = []
    error = None

    uploaded = request.files.get("csv_file")
    pasted   = request.form.get("statement_text", "").strip()

    if uploaded and uploaded.filename:
        raw = uploaded.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("windows-1255", errors="replace")
        expenses = _parse_csv_rows(text)
        if not expenses:
            error = "לא נמצאו הוצאות בקובץ. ודא שיש עמודות: date, merchant, amount"

    elif pasted:
        known = {m["display"]: m["category"] for m in db.get_all_merchants()}
        expenses = expense_parser.parse_statement(pasted, known)
        if not expenses:
            error = "לא הצלחתי לחלץ הוצאות מהטקסט."

    else:
        error = "העלה קובץ CSV או הדבק טקסט חשבון."

    if error:
        return render_template("upload.html", error=error)

    # Resolve categories + check for duplicates
    known_merchants = {m["display"].strip().lower(): m["category"] for m in db.get_all_merchants()}
    for exp in expenses:
        key = exp["merchant"].strip().lower()
        exp["category"] = known_merchants.get(key) or expense_parser.suggest_category(exp["merchant"])
        dup_id = db.find_duplicate(exp["merchant"], exp["amount"], exp.get("date"))
        exp["is_duplicate"] = dup_id is not None

    _pending = expenses
    return redirect(url_for("review"))


@app.route("/review")
def review():
    if not _pending:
        return redirect(url_for("index"))
    total     = sum(e["amount"] for e in _pending if e.get("currency", "ILS") == "ILS")
    dup_count = sum(1 for e in _pending if e.get("is_duplicate"))
    return render_template("review.html", expenses=_pending, categories=CATEGORIES,
                           total=total, dup_count=dup_count)


@app.route("/save", methods=["POST"])
def save():
    global _pending
    expenses = list(_pending)
    _pending = []

    if not expenses:
        return redirect(url_for("index"))

    selected = set(request.form.getlist("include"))
    to_save, skipped = [], 0

    for i, exp in enumerate(expenses):
        if str(i) not in selected:
            skipped += 1
            continue
        cat = request.form.get(f"category_{i}", exp.get("category", DEFAULT_CATEGORY))
        exp["category"] = cat if cat in CATEGORIES else DEFAULT_CATEGORY
        exp.setdefault("expense_date", exp.get("date"))
        exp["raw_merchant"] = exp["merchant"]
        to_save.append(exp)

    saved = db.add_expenses_batch(to_save, source="statement") if to_save else 0
    total = sum(e["amount"] for e in to_save if e.get("currency", "ILS") == "ILS")

    return render_template("done.html", saved=saved, skipped=skipped, total=total)


# ── CSV parser ─────────────────────────────────────────────────────────────────

def _parse_csv_rows(text: str) -> list[dict]:
    col_map = {
        "merchant": ["merchant", "בית עסק", "עסק", "תיאור", "description", "name"],
        "amount":   ["amount", "סכום", "חיוב", "charge", "debit"],
        "date":     ["date", "תאריך", "תאריך עסקה"],
        "currency": ["currency", "מטבע"],
    }

    def find_col(row, key):
        for candidate in col_map[key]:
            for col in row:
                if col.strip().lower() == candidate.lower():
                    return col
        return None

    expenses = []
    for row in csv.DictReader(io.StringIO(text)):
        m_col = find_col(row, "merchant")
        a_col = find_col(row, "amount")
        if not m_col or not a_col:
            continue
        merchant = str(row.get(m_col, "")).strip()
        if not merchant:
            continue
        try:
            amount = float(str(row.get(a_col, "0")).replace(",", "").replace("₪", "").strip())
        except ValueError:
            continue
        if amount <= 0:
            continue

        d_col    = find_col(row, "date")
        raw_date = str(row.get(d_col, "")).strip() if d_col else ""
        c_col    = find_col(row, "currency")
        currency = str(row.get(c_col, "ILS")).strip().upper() if c_col else "ILS"
        if currency not in {"ILS", "USD", "EUR", "GBP"}:
            currency = "ILS"

        expenses.append({
            "merchant": merchant,
            "amount":   amount,
            "currency": currency,
            "date":     expense_parser._parse_date(raw_date) if raw_date else None,
            "category": DEFAULT_CATEGORY,
        })
    return expenses


# ── Main ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    db.init_db()
    print("ממשק מקומי פועל בכתובת: http://localhost:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
