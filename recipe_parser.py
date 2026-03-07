# recipe_parser.py — extract structured recipes from text, images, or URLs
#
# Three input paths:
#   text  → DictaLM parses directly
#   image → pytesseract OCR → DictaLM parses extracted text
#   url   → fetch + strip HTML → DictaLM parses extracted text

import json
import re
import requests
from config import OLLAMA_URL, OLLAMA_MODEL

# ── Prompt ────────────────────────────────────────────────────────────────────

RECIPE_PARSE_PROMPT = """אתה מחלץ מתכונים ומחזיר JSON בלבד. אסור לכתוב טקסט נוסף.

הסכמה:
{
  "name": "שם המתכון",
  "servings": <מספר מנות, ברירת מחדל 4>,
  "ingredients": [
    {"item": "שם מרכיב", "quantity": <כמות מספרית>, "unit": "יחידה"}
  ],
  "instructions": "הוראות הכנה בטקסט חופשי"
}

חוקים:
- שמות מרכיבים בעברית בלבד
- quantity חייב להיות מספר (לא טקסט). אם לא ידוע, השתמש ב-1
- unit לדוגמה: "כפות", "כוסות", "גרם", "יחידות", "מ\"ל", "ק\"ג"
- אם אין הוראות הכנה, החזר instructions כמחרוזת ריקה
- אל תמציא מרכיבים שלא מוזכרים בטקסט

הטקסט למיצוי:
"""


def parse_recipe_from_text(text: str) -> dict | None:
    """Send raw text to DictaLM, get back a structured recipe dict. Returns None on failure."""
    prompt = RECIPE_PARSE_PROMPT + text
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 800},
            },
            timeout=60,
        )
        response.raise_for_status()
        raw = response.json().get("response", "").strip()
    except requests.RequestException as e:
        print(f"[recipe_parser] Ollama error: {e}")
        return None

    return _validate_recipe(raw)


def parse_recipe_from_image(image_bytes: bytes) -> dict | None:
    """OCR the image (Hebrew), then parse the extracted text as a recipe."""
    try:
        import pytesseract
        from PIL import Image
        import io

        image = Image.open(io.BytesIO(image_bytes))
        # lang='heb+eng' handles mixed Hebrew/English text
        text = pytesseract.image_to_string(image, lang="heb+eng")
        text = text.strip()

        if not text:
            print("[recipe_parser] OCR returned empty text")
            return None

        print(f"[recipe_parser] OCR extracted {len(text)} chars")
        return parse_recipe_from_text(text)

    except ImportError:
        print("[recipe_parser] pytesseract or Pillow not installed")
        return None
    except Exception as e:
        print(f"[recipe_parser] OCR error: {e}")
        return None


def parse_recipe_from_url(url: str) -> dict | None:
    """Fetch a URL, strip HTML, extract readable text, parse as recipe."""
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; recipe-bot/1.0)"}
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html = resp.text
    except requests.RequestException as e:
        print(f"[recipe_parser] URL fetch error: {e}")
        return None

    # Strip HTML without needing BeautifulSoup
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text[:3000]  # recipes are rarely longer than this

    if not text:
        print("[recipe_parser] No text extracted from URL")
        return None

    print(f"[recipe_parser] Extracted {len(text)} chars from URL")
    return parse_recipe_from_text(text)


# ── Validation ────────────────────────────────────────────────────────────────

def _validate_recipe(raw: str) -> dict | None:
    """Parse and validate LLM output. Returns None on schema violation."""
    clean = raw
    if "```" in clean:
        parts = clean.split("```")
        clean = parts[1] if len(parts) > 1 else parts[0]
        clean = clean.removeprefix("json").strip()

    start = clean.find("{")
    end = clean.rfind("}") + 1
    if start == -1 or end == 0:
        print(f"[recipe_parser] No JSON in response: {repr(raw[:100])}")
        return None

    try:
        parsed = json.loads(clean[start:end])
    except json.JSONDecodeError as e:
        print(f"[recipe_parser] JSON error: {e}")
        return None

    if not parsed.get("name"):
        print("[recipe_parser] Missing recipe name")
        return None

    if not isinstance(parsed.get("ingredients"), list) or not parsed["ingredients"]:
        print("[recipe_parser] Missing or empty ingredients")
        return None

    # Normalize each ingredient
    clean_ingredients = []
    for ing in parsed["ingredients"]:
        if not ing.get("item"):
            continue
        try:
            qty = float(ing.get("quantity", 1))
        except (ValueError, TypeError):
            qty = 1.0
        clean_ingredients.append({
            "item": str(ing["item"]).strip(),
            "quantity": qty,
            "unit": str(ing.get("unit", "יחידות")).strip(),
        })

    parsed["ingredients"] = clean_ingredients
    parsed["servings"] = int(parsed.get("servings", 4))
    parsed["instructions"] = str(parsed.get("instructions", "")).strip()

    print(f"[recipe_parser] Parsed: '{parsed['name']}' — {len(clean_ingredients)} ingredients")
    return parsed


# ── Display ───────────────────────────────────────────────────────────────────

def format_recipe_confirmation(recipe: dict) -> str:
    """Format a parsed recipe for user confirmation before saving."""
    lines = [f"📖 *{recipe['name']}* ({recipe['servings']} מנות)\n"]
    lines.append("*מרכיבים:*")
    for ing in recipe["ingredients"]:
        qty = int(ing["quantity"]) if ing["quantity"] == int(ing["quantity"]) else ing["quantity"]
        lines.append(f"  • {ing['item']} — {qty} {ing['unit']}")

    if recipe.get("instructions"):
        preview = recipe["instructions"][:300]
        lines.append(f"\n*הכנה:*\n{preview}")
        if len(recipe["instructions"]) > 300:
            lines.append("_[...ממשיך]_")

    lines.append("\n\nלשמור את המתכון? (כן / לא)")
    return "\n".join(lines)


def format_recipe_full(recipe: dict) -> str:
    """Format a complete saved recipe for display."""
    lines = [f"📖 *{recipe['name']}* ({recipe['servings']} מנות)\n"]
    lines.append("*מרכיבים:*")
    for ing in recipe["ingredients"]:
        qty = int(ing["quantity"]) if ing["quantity"] == int(ing["quantity"]) else ing["quantity"]
        lines.append(f"  • {ing['item']} — {qty} {ing['unit']}")

    if recipe.get("instructions"):
        lines.append(f"\n*הכנה:*\n{recipe['instructions']}")

    return "\n".join(lines)
