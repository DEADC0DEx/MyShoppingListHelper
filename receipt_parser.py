# receipt_parser.py — parse Hazi Hinam PDF receipts and extract purchased items
#
# Receipt line format (visual/display order from PDF extraction):
#   קפוס  <barcode>  <product name>  <qty ordered>  <qty supplied>  <price>  ...
#
# Only "קפוס" lines are processed (supplier lines).
# "ףילחת" lines (substitutions/deposits) are skipped.
# Processing stops at the "missing items" section near the bottom.
#
# Translation cache:
#   Raw→normalized mappings are stored in data/receipt_translations.csv so that
#   confirmed translations are reused on future imports without calling the LLM.

import csv
import os
import re
import requests
from config import OLLAMA_URL, OLLAMA_MODEL, DB_PATH

# ── Constants ─────────────────────────────────────────────────────────────────

# Prefix that marks a supplier/product line (visual order of "ספוק")
SUPPLIER_PREFIX = "קפוס"

# Prefix that marks substitution or deposit lines — skip these
SKIP_PREFIX = "ףילחת"

# Strings that mark the start of the "missing items" section — stop processing here
MISSING_SECTION_MARKERS = [
    "פריטים שלא סופקו",
    "ופקוס אל",      # visual-order "לא סופקו"
    "םירסח",         # visual-order "חסרים"
    "חסרים",
    "לא סופקו",
    "וקפוס אל",
]

# Path to the CSV translation cache (same directory as the database)
TRANSLATIONS_PATH = os.path.join(os.path.dirname(DB_PATH) or "data",
                                 "receipt_translations.csv")

# ── Translation cache (raw → normalized) ──────────────────────────────────────

_cache: dict | None = None  # lazy-loaded


def _load_cache() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    _cache = {}
    if os.path.exists(TRANSLATIONS_PATH):
        with open(TRANSLATIONS_PATH, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                raw = row.get("raw", "").strip()
                norm = row.get("normalized", "").strip()
                if raw and norm:
                    _cache[raw] = norm
    return _cache


def save_translation(raw: str, normalized: str) -> None:
    """Persist a raw→normalized mapping to the CSV cache."""
    cache = _load_cache()
    if cache.get(raw) == normalized:
        return  # already stored
    cache[raw] = normalized
    os.makedirs(os.path.dirname(TRANSLATIONS_PATH) or ".", exist_ok=True)
    with open(TRANSLATIONS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["raw", "normalized"])
        writer.writeheader()
        for r, n in cache.items():
            writer.writerow({"raw": r, "normalized": n})
    print(f"[receipt_parser] Saved translation: '{raw}' → '{normalized}'")


# ── PDF text extraction ────────────────────────────────────────────────────────

def extract_text_from_pdf(pdf_bytes: bytes) -> str:
    """Extract all text from a PDF using PyMuPDF (fitz)."""
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ImportError("PyMuPDF not installed. Run: pip install PyMuPDF")

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages = []
    for page in doc:
        text = page.get_text()
        pages.append(text)
    doc.close()
    return "\n".join(pages)


# ── Line parsing ───────────────────────────────────────────────────────────────

def parse_receipt_lines(text: str) -> list:
    """
    Parse PDF text and return list of raw product name strings.

    Only processes lines starting with SUPPLIER_PREFIX ("קפוס").
    Skips lines starting with SKIP_PREFIX ("ףילחת").
    Stops at the missing-items section.
    """
    raw_names = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Stop at the "missing items" section header
        if any(marker in stripped for marker in MISSING_SECTION_MARKERS):
            break

        # Skip substitution/deposit lines
        if stripped.startswith(SKIP_PREFIX):
            continue

        # Process only supplier lines
        if not stripped.startswith(SUPPLIER_PREFIX):
            continue

        # Split on whitespace:
        #   fields[0] = "קפוס"
        #   fields[1] = barcode number
        #   fields[2..N-3] = product name (one or more words)
        #   then trailing numeric fields (qty ordered, qty supplied, price, ...)
        fields = stripped.split()
        if len(fields) < 3:
            continue

        raw_name = _extract_product_name(fields)
        cleaned = _clean_raw_name(raw_name)
        if cleaned:
            raw_names.append(cleaned)

    return raw_names


def _extract_product_name(fields: list) -> str:
    """
    Extract the product name portion from the split fields.

    Starting at index 2 (after "קפוס" and barcode), collect words until
    we hit purely numeric tokens (quantity and price columns).
    Percentage strings like "3%" are kept as part of the name.
    """
    name_parts = []
    for token in fields[2:]:
        # A purely numeric token (integer or decimal) marks the start of qty/price fields
        if re.match(r'^\d+[\d.,]*$', token):
            break
        name_parts.append(token)
    return " ".join(name_parts)


def _clean_raw_name(raw: str) -> str:
    """
    Light cleanup of a raw product name field:
    - Remove leading/trailing whitespace then edge punctuation
    - Collapse internal whitespace
    Keep percentage strings (e.g. "3%") and hyphens intact.
    """
    # Strip whitespace first, then stray punctuation from edges, then whitespace again
    cleaned = raw.strip().strip(".,;:!?").strip()
    # Collapse internal whitespace
    cleaned = " ".join(cleaned.split())
    return cleaned


# ── LLM normalisation ─────────────────────────────────────────────────────────

NORMALIZE_PROMPT = """אתה עוזר בית. קיבלת שם מוצר גולמי מחשבונית סופרמרקט.
השם עשוי להיות בסדר ויזואלי (RTL→LTR) — כלומר האותיות של כל מילה עשויות להיות הפוכות.
החזר את שם המוצר המנורמל בעברית תקינה, בשורה אחת בלבד, ללא הסברים.
אל תוסיף מידע שאינו בשם הגולמי, אבל תקן את הכיוון ואת האיות.

דוגמאות:
  קלט: "ןוטרקב ירט בלח 3%"  → פלט: "חלב טרי 3%"
  קלט: "הנבל"               → פלט: "לבנה"
  קלט: "תיז ןמש"            → פלט: "שמן זית"
  קלט: "תוציב"              → פלט: "ביצות"
  קלט: "םחל"               → פלט: "לחם"

שם גולמי: {raw}
פלט:"""


def normalize_product_name(raw_name: str) -> tuple:
    """
    Normalize a raw product name from the receipt into clean Hebrew.

    Returns (normalized_name, is_certain):
      - is_certain=True  when the translation came from the cache or the LLM
                         returned something different from the raw input.
      - is_certain=False when the LLM failed or returned the raw name unchanged,
                         meaning a human should confirm.
    """
    # 1. Check translation cache first
    cache = _load_cache()
    if raw_name in cache:
        return cache[raw_name], True

    # 2. Call LLM
    prompt = NORMALIZE_PROMPT.format(raw=raw_name)
    try:
        response = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": 30},
            },
            timeout=15,
        )
        response.raise_for_status()
        result = response.json().get("response", "").strip()
        # Take only the first line in case the model adds extra text
        result = result.splitlines()[0].strip() if result else ""
    except requests.RequestException as e:
        print(f"[receipt_parser] Ollama error normalizing '{raw_name}': {e}")
        return raw_name, False  # uncertain — human should confirm

    if not result or result == raw_name:
        # LLM didn't help — flag for human confirmation
        return raw_name, False

    # Cache the successful LLM result for future imports
    save_translation(raw_name, result)
    return result, True


# ── Public API ────────────────────────────────────────────────────────────────

def import_receipt(pdf_bytes: bytes) -> tuple:
    """
    Parse a Hazi Hinam PDF receipt and classify each extracted product.

    Returns (certain, uncertain):
      certain   — list of clean Hebrew names the LLM (or cache) normalized
                  confidently; call db.set_inventory_status(name, "יש") for each.
      uncertain — list of raw product name strings the LLM couldn't normalize;
                  present to the user for manual confirmation via receipt_flow.
    """
    text = extract_text_from_pdf(pdf_bytes)
    raw_names = parse_receipt_lines(text)

    print(f"[receipt_parser] Found {len(raw_names)} product lines in receipt")

    certain = []
    uncertain = []
    for raw in raw_names:
        name, is_certain = normalize_product_name(raw)
        print(f"[receipt_parser]   '{raw}' → '{name}' (certain={is_certain})")
        if is_certain:
            certain.append(name)
        else:
            uncertain.append(raw)

    return certain, uncertain
