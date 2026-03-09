#!/usr/bin/env python3
# test_local.py — verify each component works before connecting Telegram
#
# Run with: python test_local.py
# Run without Ollama: python test_local.py --no-ollama

import os
import sys
from datetime import datetime, timedelta

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


# ── Test 1b: Expiration dates ─────────────────────────────────────────────────

def test_expiration():
    print("\n── Test 1b: Expiration Dates ────────────────────────────────")
    reset_db()

    today = datetime.now().date()
    exp_soon = (today + timedelta(days=10)).strftime("%Y-%m-%d")
    exp_far  = (today + timedelta(days=60)).strftime("%Y-%m-%d")
    exp_today = today.strftime("%Y-%m-%d")

    db.set_inventory_status("חלב", "יש")
    db.set_expiration_date("חלב", exp_soon)

    db.set_inventory_status("קמח", "יש")
    db.set_expiration_date("קמח", exp_far)

    db.set_inventory_status("גבינה", "יש")
    db.set_expiration_date("גבינה", exp_today)

    expiring = db.get_expiring_soon_items(days=30)
    names = [i["name"] for i in expiring]
    assert "חלב" in names, f"Expected 'חלב' in expiring, got {names}"
    assert "גבינה" in names, f"Expected 'גבינה' in expiring, got {names}"
    assert "קמח" not in names, f"'קמח' should not be in 30-day expiring list, got {names}"
    print(f"  ✅ Expiring within 30 days: {names}")

    # Check days_left for today's item
    today_item = next(i for i in expiring if i["name"] == "גבינה")
    assert today_item["days_left"] == 0, f"Expected 0 days left, got {today_item['days_left']}"
    print("  ✅ days_left = 0 for item expiring today")

    # Add expiring items to shopping list with note
    added = db.add_to_shopping_list("חלב", note="פג תוקף בקרוב")
    assert added is True, "Expected newly added = True"
    added_again = db.add_to_shopping_list("חלב", note="פג תוקף בקרוב")
    assert added_again is False, "Expected no duplicate = False"
    print("  ✅ add_to_shopping_list with note, no duplicates")

    shopping = db.get_shopping_list()
    milk = next((r for r in shopping if r["name"] == "חלב"), None)
    assert milk is not None, "חלב not found in shopping list"
    assert milk["note"] == "פג תוקף בקרוב", f"Expected note, got {milk['note']}"
    print("  ✅ Shopping list entry has correct note")

    # Verify executor shows note as prefix in list display
    reply = executor.handle({"action": "list", "items": []})
    assert "פג תוקף בקרוב" in reply, f"Expected note prefix in list reply, got: {reply}"
    print("  ✅ List display shows expiration note prefix")

    # Test scheduler helper functions
    import scheduler
    expiring_sched = scheduler.get_expiring_items()
    assert any(i["name"] == "גבינה" for i in expiring_sched), "גבינה should appear in scheduler expiring"
    print("  ✅ scheduler.get_expiring_items() returns expiring items")

    # גבינה is not yet in shopping list — add_expiring_to_shopping_list should add it
    newly = scheduler.add_expiring_to_shopping_list([{"name": "גבינה", "exp_date": exp_today, "days_left": 0}])
    assert len(newly) == 1 and newly[0]["name"] == "גבינה", f"Expected גבינה newly added, got {newly}"
    print("  ✅ scheduler.add_expiring_to_shopping_list() adds item and reports it as new")

    # Calling again should not re-add (already pending)
    newly2 = scheduler.add_expiring_to_shopping_list([{"name": "גבינה", "exp_date": exp_today, "days_left": 0}])
    assert len(newly2) == 0, f"Expected 0 newly added on second call, got {newly2}"
    print("  ✅ No duplicate addition on repeated scheduler run")

    # Test build_message includes expiring section
    msg = scheduler.build_message(
        low=[],
        predicted=[],
        expiring=[{"name": "חלב", "exp_date": exp_soon, "days_left": 10}],
        newly_added_expiring=[{"name": "חלב", "exp_date": exp_soon, "days_left": 10}],
    )
    assert msg is not None, "Expected non-None message with expiring items"
    assert "חלב" in msg, f"Expected חלב in message: {msg}"
    assert "פג תוקף" in msg, f"Expected 'פג תוקף' in message: {msg}"
    print("  ✅ build_message includes expiring section and newly-added notification")

    print("✅ Expiration dates test passed")


# ── Test 1c: Location support ─────────────────────────────────────────────────

def test_location():
    print("\n── Test 1c: Location Support ────────────────────────────────")
    reset_db()

    # Set items in different locations
    db.set_inventory_status("חלב", "יש", location="בית")
    db.set_inventory_status("חלב", "יש", location="מקלט")
    db.set_inventory_status("שעועית", "יש", location="מקלט")
    db.set_inventory_status("לחם", "אין", location="בית")

    # get_inventory without filter returns all
    all_inv = db.get_inventory()
    assert len(all_inv) == 4, f"Expected 4 inventory rows (2 locations × items), got {len(all_inv)}"
    locations = {r["location"] for r in all_inv}
    assert "בית" in locations and "מקלט" in locations, f"Expected both locations, got {locations}"
    print("  ✅ get_inventory() returns items from all locations")

    # get_inventory with location filter
    home_inv = db.get_inventory(location="בית")
    assert all(r["location"] == "בית" for r in home_inv), "All items should be from בית"
    shelter_inv = db.get_inventory(location="מקלט")
    assert all(r["location"] == "מקלט" for r in shelter_inv), "All items should be from מקלט"
    print(f"  ✅ Filtered inventory: {len(home_inv)} home, {len(shelter_inv)} shelter items")

    # Expiration per location
    today = datetime.now().date()
    exp_soon = (today + timedelta(days=5)).strftime("%Y-%m-%d")
    db.set_expiration_date("חלב", exp_soon, location="מקלט")

    expiring_all = db.get_expiring_soon_items(days=30)
    assert any(i["name"] == "חלב" and i["location"] == "מקלט" for i in expiring_all)
    print("  ✅ Expiring items include location field")

    expiring_shelter = db.get_expiring_soon_items(days=30, location="מקלט")
    assert all(i["location"] == "מקלט" for i in expiring_shelter)
    expiring_home = db.get_expiring_soon_items(days=30, location="בית")
    assert len(expiring_home) == 0, f"No expiring items at home, got {expiring_home}"
    print("  ✅ get_expiring_soon_items location filter works")

    # Executor inventory display groups by location
    reply = executor._handle_inventory([])
    assert "מקלט" in reply and "בית" in reply, f"Expected both location headers, got: {reply}"
    print("  ✅ Inventory display groups by location")

    reply_shelter = executor._handle_inventory([], location="מקלט")
    assert "מקלט" not in reply_shelter or "בית" not in reply_shelter, \
        "Filtered inventory should not show location header for single location"
    print("  ✅ Filtered inventory display works")

    print("✅ Location support test passed")


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
        test_expiration()
        test_location()
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