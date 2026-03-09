#!/usr/bin/env python3
# test_local.py — verify each component works before connecting Telegram
#
# Run with: python test_local.py
# Run without Ollama: python test_local.py --no-ollama

import os
import sys

# Ensure data dir exists before anything imports config
os.makedirs("data", exist_ok=True)

# Point at a test DB for the whole test run — must happen before any other import
import config
config.DB_PATH = "data/test.db"

# Now safe to import everything else
import db
import executor
import intent_parser
import receipt_parser
import receipt_flow


def reset_db():
    """Drop and recreate the test database cleanly."""
    if os.path.exists(config.DB_PATH):
        os.remove(config.DB_PATH)
    db.init_db()


# ── Test 1: Database ──────────────────────────────────────────────────────────

def test_database():
    print("\n── Test 1: Database ─────────────────────────────────────────")
    reset_db()

    db.set_inventory_status("חלב", "יש")
    db.set_inventory_status("ביצים", "יש")
    db.set_inventory_status("לחם", "אין")

    inventory = db.get_inventory()
    assert len(inventory) == 3, f"Expected 3 items, got {len(inventory)}"
    print(f"  ✅ Inventory: {[r['name'] for r in inventory]}")

    db.add_to_shopping_list("לחם")
    db.add_to_shopping_list("חלב")
    shopping = db.get_shopping_list()
    assert len(shopping) == 2, f"Expected 2 items, got {len(shopping)}"
    print(f"  ✅ Shopping list: {[r['name'] for r in shopping]}")

    # Adding same item twice should not duplicate
    db.add_to_shopping_list("לחם")
    shopping = db.get_shopping_list()
    assert len(shopping) == 2, f"Duplicate check failed, got {len(shopping)} items"
    print("  ✅ No duplicates in shopping list")

    db.remove_from_shopping_list("לחם")
    shopping = db.get_shopping_list()
    assert len(shopping) == 1, f"Expected 1 item after remove, got {len(shopping)}"
    print(f"  ✅ After remove: {[r['name'] for r in shopping]}")

    print("✅ Database test passed")


# ── Test 2: Executor ──────────────────────────────────────────────────────────

def test_executor():
    print("\n── Test 2: Executor ─────────────────────────────────────────")
    reset_db()

    reply = executor.handle({"action": "add", "items": ["חלב", "ביצים"]})
    print(f"  add       → {reply}")
    assert "חלב" in reply and "ביצים" in reply
    print("  ✅ add works")

    reply = executor.handle({"action": "depleted", "items": ["לחם"]})
    print(f"  depleted  → {reply}")
    assert "לחם" in reply
    print("  ✅ depleted works")

    reply = executor.handle({"action": "low", "items": ["שמן"]})
    print(f"  low       → {reply}")
    assert "שמן" in reply
    print("  ✅ low works")

    reply = executor.handle({"action": "list", "items": []})
    print(f"  list      → {reply}")
    assert "לחם" in reply or "שמן" in reply
    print("  ✅ list works")

    reply = executor.handle({"action": "inventory", "items": []})
    print(f"  inventory → {reply}")
    assert "חלב" in reply
    print("  ✅ inventory works")

    reply = executor.handle({"action": "remove", "items": ["לחם"]})
    print(f"  remove    → {reply}")
    assert "לחם" in reply
    print("  ✅ remove works")

    reply = executor.handle({"action": "unknown", "items": []})
    print(f"  unknown   → {reply[:60]}...")
    print("  ✅ unknown fallback works")

    print("✅ Executor test passed")


# ── Test 2b: Receipt Parser (unit tests — no Ollama, no PDF file) ─────────────

def test_receipt_parser():
    print("\n── Test 2b: Receipt Parser ──────────────────────────────────")

    # ── parse_receipt_lines ────────────────────────────────────────────────────

    # Typical Hazi Hinam receipt text extracted by PyMuPDF.
    # Words are in visual left-to-right order (Hebrew text appears reversed).
    sample_text = """\
ספק: חצי חינם
תאריך: 01/01/2024

פריטים שסופקו:
קפוס 7290000066885 ןוטרקב ירט בלח 3% 2 2 11.90 א
קפוס 7290010935651 תוציב 12 1 1 14.90 א
קפוס 7290000197500 תיז ןמש 750 1 1 22.50 א
ףילחת 7290001254383 ריחמ ןוקית 1 1 3.00 א
קפוס 7290000228922 הנבל 5% 1 1 8.90 א

פריטים שלא סופקו:
קפוס 1111111111111 רצומ רסח 1 0 0.00 א
"""

    names = receipt_parser.parse_receipt_lines(sample_text)

    # Should extract 4 items (not the ףילחת line, not the missing-section line)
    assert len(names) == 4, f"Expected 4 items, got {len(names)}: {names}"
    print(f"  ✅ Extracted {len(names)} items (skipped substitution + missing section)")
    print(f"     {names}")

    # ── _extract_product_name ──────────────────────────────────────────────────

    fields_milk = ["קפוס", "7290000066885", "ןוטרקב", "ירט", "בלח", "3%", "2", "2", "11.90"]
    name = receipt_parser._extract_product_name(fields_milk)
    assert name == "ןוטרקב ירט בלח 3%", f"Got: '{name}'"
    print(f"  ✅ _extract_product_name: '{name}'")

    # Single-word product
    fields_eggs = ["קפוס", "7290010935651", "תוציב", "12", "1", "1", "14.90"]
    name = receipt_parser._extract_product_name(fields_eggs)
    assert name == "תוציב", f"Got: '{name}'"
    print(f"  ✅ _extract_product_name (single word): '{name}'")

    # ── _clean_raw_name ────────────────────────────────────────────────────────

    assert receipt_parser._clean_raw_name("  בלח.  ") == "בלח"
    assert receipt_parser._clean_raw_name("תיז ןמש") == "תיז ןמש"
    assert receipt_parser._clean_raw_name("בלח 3%") == "בלח 3%"
    print("  ✅ _clean_raw_name strips edge punctuation correctly")

    # ── Supplier-prefix filtering ──────────────────────────────────────────────

    only_supplier = """\
כותרת כלשהי
ףילחת 111 תחליף-מוצר 1 1 5.00
קפוס 222 םחל 1 1 3.50
קפוס 333 הנבל 2 2 8.00
שורה כללית ללא קידומת
"""
    names2 = receipt_parser.parse_receipt_lines(only_supplier)
    assert len(names2) == 2, f"Expected 2, got {len(names2)}: {names2}"
    print(f"  ✅ Supplier-prefix filter: {names2}")

    # ── Missing-section stop ───────────────────────────────────────────────────

    with_missing = """\
קפוס 100 ירפ 1 1 4.00
פריטים שלא סופקו
קפוס 200 רסח 1 0 0.00
"""
    names3 = receipt_parser.parse_receipt_lines(with_missing)
    assert len(names3) == 1, f"Expected 1 (stop before missing section), got {len(names3)}"
    print(f"  ✅ Stops at missing-items section: {names3}")

    print("✅ Receipt parser test passed")


# ── Test 2c: Translation cache ────────────────────────────────────────────────

def test_translation_cache():
    print("\n── Test 2c: Translation cache ───────────────────────────────")

    # Point the cache at a temp file so we don't pollute the real one
    original_path = receipt_parser.TRANSLATIONS_PATH
    test_csv = "data/test_translations.csv"
    receipt_parser.TRANSLATIONS_PATH = test_csv
    receipt_parser._cache = None  # reset lazy cache

    try:
        # File doesn't exist yet — cache should be empty
        cache = receipt_parser._load_cache()
        assert cache == {}, f"Expected empty cache, got {cache}"
        print("  ✅ Empty cache on missing file")

        # Save a translation
        receipt_parser.save_translation("תוציב", "ביצות")
        receipt_parser.save_translation("הנבל", "לבנה")

        # Reload and verify
        receipt_parser._cache = None
        cache = receipt_parser._load_cache()
        assert cache.get("תוציב") == "ביצות", f"Got: {cache}"
        assert cache.get("הנבל") == "לבנה"
        print(f"  ✅ Saved and reloaded {len(cache)} translations")

        # Saving the same entry again must not duplicate it
        receipt_parser.save_translation("תוציב", "ביצות")
        receipt_parser._cache = None
        cache2 = receipt_parser._load_cache()
        assert len(cache2) == 2, f"Expected 2, got {len(cache2)}"
        print("  ✅ No duplicates on repeated save")

    finally:
        # Restore state
        receipt_parser.TRANSLATIONS_PATH = original_path
        receipt_parser._cache = None
        if os.path.exists(test_csv):
            os.remove(test_csv)

    print("✅ Translation cache test passed")


# ── Test 2d: Receipt flow (stateful confirmation) ─────────────────────────────

def test_receipt_flow():
    print("\n── Test 2d: Receipt flow ────────────────────────────────────")
    reset_db()

    # Point translations at a temp file
    original_path = receipt_parser.TRANSLATIONS_PATH
    test_csv = "data/test_flow_translations.csv"
    receipt_parser.TRANSLATIONS_PATH = test_csv
    receipt_parser._cache = None

    try:
        CHAT = 999

        # Pre-populate shopping list — both items should be removed after confirmation
        db.add_to_shopping_list("ביצות")
        db.add_to_shopping_list("לבנה")

        # Start with two uncertain items
        reply = receipt_flow.start(CHAT, ["תוציב", "הנבל"])
        assert receipt_flow.is_active(CHAT)
        assert "תוציב" in reply
        print(f"  ✅ Started flow, first question: {reply[:60]!r}...")

        # User confirms the first item
        reply = receipt_flow.handle(CHAT, "ביצות")
        assert "הנבל" in reply  # moved to next item
        print(f"  ✅ Confirmed first item, next question shown")

        # User skips the second item
        reply = receipt_flow.handle(CHAT, "דלג")
        assert not receipt_flow.is_active(CHAT)  # conversation ended
        assert "1" in reply  # 1 item confirmed
        print(f"  ✅ Skipped second item, flow ended: {reply!r}")

        # Verify the confirmed item was saved to inventory
        inventory = db.get_inventory()
        names = [r["name"] for r in inventory]
        assert "ביצות" in names, f"Expected 'ביצות' in inventory, got {names}"
        print("  ✅ Confirmed item added to inventory")

        # Verify "ביצות" was removed from shopping list; "לבנה" (skipped) still there
        shopping = [r["name"] for r in db.get_shopping_list()]
        assert "ביצות" not in shopping, f"Expected 'ביצות' removed from list, got {shopping}"
        assert "לבנה" in shopping, f"Expected 'לבנה' still in list, got {shopping}"
        print("  ✅ Confirmed item removed from shopping list, skipped item kept")

        # Verify translation was saved
        receipt_parser._cache = None
        cache = receipt_parser._load_cache()
        assert cache.get("תוציב") == "ביצות", f"Cache: {cache}"
        assert "הנבל" not in cache  # skipped item not saved
        print("  ✅ Translation saved for confirmed item, skipped item not saved")

    finally:
        receipt_parser.TRANSLATIONS_PATH = original_path
        receipt_parser._cache = None
        if os.path.exists(test_csv):
            os.remove(test_csv)

    print("✅ Receipt flow test passed")


# ── Test 3: Intent Parser (requires Ollama) ───────────────────────────────────

def test_intent_parser():
    print("\n── Test 3: Intent Parser (Ollama) ───────────────────────────")
    print("   Calling Ollama — this may take a few seconds per message.\n")

    test_cases = [
        ("קניתי חלב וביצים",   "add",       ["חלב", "ביצים"]),
        ("נגמר הלחם",          "depleted",  ["לחם"]),
        ("החמאה על הסף",       "low",       ["חמאה"]),
        ("מה צריך לקנות?",     "list",      []),
        ("מה יש בבית?",        "inventory", []),
        ("מה אפשר לבשל?",      "recipe",    []),
    ]

    passed = 0
    failed = 0

    for message, expected_action, expected_items in test_cases:
        result = intent_parser.parse_intent(message)
        action_ok = result["action"] == expected_action
        items_ok = all(item in result["items"] for item in expected_items)
        ok = action_ok and items_ok

        status = "✅" if ok else "❌"
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"  {status} '{message}'")
        if not ok:
            print(f"       expected: action={expected_action}, items={expected_items}")
            print(f"       got:      action={result['action']}, items={result['items']}")

    print(f"\n  Results: {passed}/{len(test_cases)} passed")
    if failed > 0:
        print("  ⚠️  Some cases failed — check the prompt in intent_parser.py")
    else:
        print("✅ Intent parser test passed")


# ── Test 4: Full flow ─────────────────────────────────────────────────────────

def test_full_flow():
    print("\n── Test 4: Full flow (Ollama → Executor) ────────────────────")
    reset_db()

    messages = [
        "קניתי היום חלב, לחם ועגבניות",
        "נגמרו הביצים",
        "מה צריך לקנות?",
        "מה יש בבית?",
    ]

    for msg in messages:
        print(f"\n  👤 {msg}")
        parsed = intent_parser.parse_intent(msg)
        reply = executor.handle(parsed)
        print(f"  🤖 {reply}")

    print("\n✅ Full flow test complete")


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("Grocery Bot — Component Tests")
    print("=" * 60)

    run_ollama = "--no-ollama" not in sys.argv

    try:
        test_database()
        test_executor()
        test_receipt_parser()
        test_translation_cache()
        test_receipt_flow()
        if run_ollama:
            test_intent_parser()
            test_full_flow()
        else:
            print("\n⏭️  Skipping Ollama tests (--no-ollama flag set)")

    except AssertionError as e:
        print(f"\n❌ Assertion failed: {e}")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
    finally:
        # Always clean up test DB
        if os.path.exists(config.DB_PATH):
            os.remove(config.DB_PATH)

    print("\n" + "=" * 60)
    if run_ollama:
        print("All tests passed. Next step:")
        print("  1. Add your Telegram token to config.py")
        print("  2. Run: python bot.py")
    else:
        print("DB + Executor tests passed.")
        print("Run without --no-ollama to test the full LLM flow.")
    print("=" * 60)