# server.py — local web server for the shopping list
#
# Run:  python server.py
# Open: http://localhost:5000
#
# All data lives in the same SQLite database the Telegram bot uses (data/grocery.db).
# The bot and the web UI can run side-by-side and share the same data.

import io
import os
from datetime import datetime
from flask import Flask, jsonify, request, render_template, send_file

import db
import receipt_parser

app = Flask(__name__)

db.init_db()


# ── Shopping list ──────────────────────────────────────────────────────────────

@app.get("/api/list")
def api_list_get():
    rows = db.get_shopping_list()
    return jsonify([r["name"] for r in rows])


@app.post("/api/list")
def api_list_add():
    name = (request.json or {}).get("name", "").strip()
    if not name:
        return jsonify({"error": "name required"}), 400
    db.add_to_shopping_list(name)
    return jsonify({"ok": True})


@app.delete("/api/list/<path:name>")
def api_list_remove(name):
    db.remove_from_shopping_list(name)
    return jsonify({"ok": True})


@app.post("/api/list/buy/<path:name>")
def api_list_buy(name):
    """Mark item as bought: set inventory to יש and remove from list."""
    db.set_inventory_status(name, "יש")
    db.remove_from_shopping_list(name)
    return jsonify({"ok": True})


@app.post("/api/list/reorder")
def api_list_reorder():
    """Accept an ordered array of item names and persist the order."""
    names = request.json or []
    # Rebuild the list in the given order: remove all then re-add
    for name in names:
        db.remove_from_shopping_list(name)
    for name in names:
        db.add_to_shopping_list(name)
    return jsonify({"ok": True})


# ── Inventory ─────────────────────────────────────────────────────────────────

@app.get("/api/inventory")
def api_inventory():
    rows = db.get_inventory()
    return jsonify([{"name": r["name"], "status": r["status"]} for r in rows])


@app.post("/api/inventory/<path:name>")
def api_inventory_set(name):
    status = (request.json or {}).get("status", "יש")
    if status not in ("יש", "נמוך", "אין"):
        return jsonify({"error": "invalid status"}), 400
    db.set_inventory_status(name, status)
    if status in ("נמוך", "אין"):
        db.add_to_shopping_list(name)
    elif status == "יש":
        db.remove_from_shopping_list(name)
    return jsonify({"ok": True})


# ── PDF import ────────────────────────────────────────────────────────────────

@app.post("/api/import")
def api_import():
    """
    Receive a PDF, parse it, and return the list of found item names.
    The caller then POSTs /api/import/apply with the confirmed list.
    """
    if "file" not in request.files:
        return jsonify({"error": "no file"}), 400
    f = request.files["file"]
    if not f.filename.lower().endswith(".pdf"):
        return jsonify({"error": "PDF only"}), 400

    pdf_bytes = f.read()
    text = receipt_parser.extract_text_from_pdf(pdf_bytes)
    fmt  = receipt_parser.detect_format(text)

    if fmt == "unknown":
        return jsonify({"error": "פורמט לא מזוהה. ודא שזו חשבונית שופרסל או חצי חינם."}), 422

    certain, uncertain = receipt_parser.import_receipt(pdf_bytes)

    # For shufersal + OCR formats, uncertain is always []
    # For hazi-hinam the LLM may leave some for the user to confirm
    all_names = certain + uncertain
    return jsonify({
        "format":    fmt,
        "certain":   certain,
        "uncertain": uncertain,
        "all":       all_names,
    })


@app.post("/api/import/apply")
def api_import_apply():
    """Apply a confirmed list of item names: update inventory + remove from list."""
    names = (request.json or {}).get("items", [])
    for name in names:
        db.set_inventory_status(name, "יש")
        db.remove_from_shopping_list(name)
    return jsonify({"ok": True, "count": len(names)})


# ── Export ────────────────────────────────────────────────────────────────────

@app.get("/api/export")
def api_export():
    """Return a self-contained HTML shopping list the user can save to their phone."""
    rows = db.get_shopping_list()
    items = [r["name"] for r in rows]
    now   = datetime.now().strftime("%d/%m/%Y %H:%M")

    rows_html = "\n".join(
        f'    <li><label><input type="checkbox"> {item}</label></li>'
        for item in items
    )

    html = f"""<!DOCTYPE html>
<html dir="rtl" lang="he">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>רשימת קניות — {now}</title>
  <style>
    body {{
      font-family: 'Segoe UI', Tahoma, Arial, sans-serif;
      background: #f7f9fc;
      color: #1a1a2e;
      max-width: 480px;
      margin: 0 auto;
      padding: 20px 16px 40px;
    }}
    h1 {{ font-size: 1.5rem; margin-bottom: 4px; }}
    .date {{ color: #888; font-size: .85rem; margin-bottom: 20px; }}
    ul {{ list-style: none; padding: 0; }}
    li {{
      background: #fff;
      border-radius: 10px;
      padding: 14px 16px;
      margin-bottom: 8px;
      box-shadow: 0 1px 4px rgba(0,0,0,.08);
      font-size: 1.05rem;
    }}
    label {{ display: flex; align-items: center; gap: 12px; cursor: pointer; }}
    input[type=checkbox] {{ width: 20px; height: 20px; cursor: pointer; }}
    input:checked + span {{ text-decoration: line-through; color: #aaa; }}
  </style>
</head>
<body>
  <h1>🛒 רשימת קניות</h1>
  <div class="date">יוצא: {now} &nbsp;|&nbsp; {len(items)} פריטים</div>
  <ul>
{rows_html}
  </ul>
</body>
</html>"""

    buf = io.BytesIO(html.encode("utf-8"))
    filename = f"shopping_list_{datetime.now().strftime('%Y%m%d_%H%M')}.html"
    return send_file(buf, mimetype="text/html",
                     as_attachment=True, download_name=filename)


# ── Serve the app ─────────────────────────────────────────────────────────────

@app.get("/")
def index():
    return render_template("app.html")


if __name__ == "__main__":
    port = int(os.getenv("PORT", 5000))
    print(f"\n🛒  Shopping list server running → http://localhost:{port}\n")
    app.run(host="0.0.0.0", port=port, debug=False)
