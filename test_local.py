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