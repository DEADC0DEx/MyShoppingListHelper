# receipt_parser.py — parse Hazi Hinam PDF receipts and extract purchased items
#
# Receipt line format (visual/display order from PDF extraction):
#   קפוס  <barcode>  <product name>  <qty ordered>  <qty supplied>  <price>  ...
#
# Only "קפוס" lines are processed (supplier lines).
# "ףילחת" lines (substitutions/deposits) are skipped.
# Processing stops at the "missing items" section near the bottom.

import re
import requests
from config import OLLAMA_URL, OLLAMA_MODEL

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


def normalize_product_name(raw_name: str) -> str:
    """
    Use the LLM to normalize a raw product name from the receipt into clean Hebrew.
    Falls back to the raw name if the LLM call fails.
    """
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
        return result if result else raw_name
    except requests.RequestException as e:
        print(f"[receipt_parser] Ollama error normalizing '{raw_name}': {e}")
        return raw_name  # Fall back to raw name so import still works


# ── Public API ────────────────────────────────────────────────────────────────

def import_receipt(pdf_bytes: bytes) -> list:
    """
    Parse a Hazi Hinam PDF receipt and return a list of normalized product names.

    Each name is ready to be passed to db.set_inventory_status(name, "יש").
    Returns an empty list if no items could be extracted.
    """
    text = extract_text_from_pdf(pdf_bytes)
    raw_names = parse_receipt_lines(text)

    print(f"[receipt_parser] Found {len(raw_names)} product lines in receipt")

    normalized = []
    for raw in raw_names:
        name = normalize_product_name(raw)
        print(f"[receipt_parser]   '{raw}' → '{name}'")
        normalized.append(name)

    return normalized
