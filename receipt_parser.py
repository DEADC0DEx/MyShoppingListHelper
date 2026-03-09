# receipt_parser.py — parse receipt PDFs and Pairzon HTML receipts
#
# Supported formats
# ─────────────────
# "hazi_hinam"  : Hazi Hinam PDF (reversed Hebrew, existing logic)
#                 Detection: text contains "קפוס" (visual-order "ספוק")
#
# "shufersal"   : Shufersal PDF (clean Hebrew, fixed-width columns)
#                 Columns: קוד פריט | תאור | הוזמן | סופק | מחיר | סה"כ
#                 Detection: text contains "שופרסל" or ("תאור" + "סופק")
#
# "pairzon_url" : Pairzon live HTML receipt (URL sent by user)
#                 Detection: text contains an osher.pairzon.com URL
#                 Parsing: requests GET → html.parser table extraction
#
# Translation cache:
#   Raw→normalised mappings stored in data/receipt_translations.csv so that
#   confirmed translations are reused on future imports without an LLM call.

import csv
import os
import re
import requests
from html.parser import HTMLParser

from config import OLLAMA_URL, OLLAMA_MODEL, TESSERACT_LANG, DB_PATH

# ── Constants ─────────────────────────────────────────────────────────────────

# Hazi Hinam: prefix marking a supplier/product line (visual order of "ספוק")
SUPPLIER_PREFIX = "קפוס"

# Hazi Hinam: prefix marking substitution / deposit lines — skip these
SKIP_PREFIX = "ףילחת"

# Hazi Hinam: strings that mark the start of the "missing items" section
MISSING_SECTION_MARKERS = [
    "פריטים שלא סופקו",
    "ופקוס אל",      # visual-order "לא סופקו"
    "םירסח",         # visual-order "חסרים"
    "חסרים",
    "לא סופקו",
    "וקפוס אל",
]

# Pairzon receipt URL pattern
PAIRZON_URL_RE = re.compile(r'https?://\S*pairzon\.com/\S+', re.IGNORECASE)

# Path to the CSV translation cache
TRANSLATIONS_PATH = os.path.join(os.path.dirname(DB_PATH) or "data",
                                 "receipt_translations.csv")

# ── Translation cache (raw → normalised) ──────────────────────────────────────

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
    """Persist a raw→normalised mapping to the CSV cache."""
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


# ── Format detection ───────────────────────────────────────────────────────────

def detect_format(text: str) -> str:
    """
    Detect receipt format from text (PDF-extracted or a user message).

    Returns one of:
      "pairzon_url" — text contains an osher.pairzon.com URL
      "shufersal"   — clean Hebrew PDF with column headers תאור / סופק
      "hazi_hinam"  — existing reversed-Hebrew Hazi Hinam PDF format
      "unknown"     — no recognised pattern

    Shufersal detection uses three independent signals (any one is enough):
      1. Store name "שופרסל" appears in the text.
      2. Both column headers "תאור" and ("סופק" or "הוזמן") are present.
      3. EAN-13 barcodes (13 consecutive digits) appear in a receipt that
         is NOT Hazi Hinam (i.e. the reversed-Hebrew "קפוס" marker is absent).
         This handles PDFs whose logo/headers are stored as images and
         therefore produce no extractable text for the store name or headers.
    """
    if PAIRZON_URL_RE.search(text):
        return "pairzon_url"

    # Shufersal signal 1 & 2: explicit store name or column headers
    if "שופרסל" in text:
        return "shufersal"
    if "תאור" in text and ("סופק" in text or "הוזמן" in text):
        return "shufersal"

    # Hazi Hinam: reversed-Hebrew "ספוק" marker
    if SUPPLIER_PREFIX in text:
        return "hazi_hinam"

    # Shufersal signal 3: EAN-13 barcodes present but no Hazi Hinam marker
    if re.search(r'\b\d{13}\b', text):
        return "shufersal"

    return "unknown"


# ── Hazi Hinam parser (existing) ──────────────────────────────────────────────

def parse_receipt_lines(text: str) -> list:
    """
    Parse Hazi Hinam PDF text and return list of raw product name strings.

    Only processes lines starting with SUPPLIER_PREFIX ("קפוס").
    Skips lines starting with SKIP_PREFIX ("ףילחת").
    Stops at the missing-items section.
    """
    raw_names = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        if any(marker in stripped for marker in MISSING_SECTION_MARKERS):
            break

        if stripped.startswith(SKIP_PREFIX):
            continue

        if not stripped.startswith(SUPPLIER_PREFIX):
            continue

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
    Extract the product name portion from the split Hazi Hinam fields.

    Starting at index 2 (after "קפוס" and barcode), collect words until
    we hit purely numeric tokens (quantity and price columns).
    Percentage strings like "3%" are kept as part of the name.
    """
    name_parts = []
    for token in fields[2:]:
        if re.match(r'^\d+[\d.,]*$', token):
            break
        name_parts.append(token)
    return " ".join(name_parts)


def _clean_raw_name(raw: str) -> str:
    """
    Light cleanup of a raw product name field.
    Strips edge whitespace and punctuation; collapses internal whitespace.
    """
    cleaned = raw.strip().strip(".,;:!?").strip()
    cleaned = " ".join(cleaned.split())
    return cleaned


# ── Shufersal parser ──────────────────────────────────────────────────────────

# Keywords whose presence on a line means it should be skipped entirely
_SHUFERSAL_SKIP_KEYWORDS = [
    "מבצע", "סך הכל", 'סה"כ', "מע\"מ", "תאור", "קוד פריט",
    "הוזמן", "מחיר", "סכום", "סיכום", "לתשלום", "שופרסל",
]


def parse_shufersal(text: str) -> list:
    """
    Parse a Shufersal PDF receipt (clean Hebrew, fixed-width columns).

    Column order: קוד פריט | תאור | הוזמן | סופק | מחיר | סה"כ
    Only returns items where סופק (supplied qty) > 0.
    Returns list of {"name": str, "qty": float}.
    """
    items = []

    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue

        # Skip separator/divider lines
        if set(stripped) <= {'-', '=', ' ', '|'}:
            continue

        # Skip header, summary, and discount lines
        if any(kw in stripped for kw in _SHUFERSAL_SKIP_KEYWORDS):
            continue

        tokens = stripped.split()
        if len(tokens) < 4:
            continue

        # First token must be a product barcode (6+ consecutive digits)
        if not re.match(r'^\d{6,}$', tokens[0]):
            continue

        # Collect trailing numeric tokens right-to-left until a non-numeric token
        name_end = len(tokens)
        trailing = []
        for i in range(len(tokens) - 1, 0, -1):
            if re.match(r'^\d+([.,]\d+)?$', tokens[i]):
                trailing.insert(0, tokens[i])
                name_end = i
            else:
                break

        # Need at least qty_ordered + qty_supplied
        if len(trailing) < 2:
            continue

        name = " ".join(tokens[1:name_end]).strip()
        if not name:
            continue

        # Trailing order (left→right): הוזמן | סופק | מחיר | סה"כ
        # From right (index -n):         [-4]  | [-3] | [-2] | [-1]
        # qty_supplied is the 3rd from the right when ≥3 trailing fields,
        # otherwise the 1st (when only 2: הוזמן | סופק).
        try:
            if len(trailing) >= 3:
                qty_supplied = float(trailing[-3].replace(',', '.'))
            else:
                qty_supplied = float(trailing[1].replace(',', '.'))
        except (ValueError, IndexError):
            continue

        if qty_supplied <= 0:
            continue

        items.append({"name": name, "qty": qty_supplied})

    return items


# ── Pairzon HTML parser ────────────────────────────────────────────────────────

class _TableParser(HTMLParser):
    """Minimal HTML table extractor (no external dependencies)."""

    def __init__(self):
        super().__init__()
        self.tables: list[list[list[str]]] = []
        self._table: list[list[str]] | None = None
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag, attrs):
        if tag == "table":
            self._table = []
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag):
        if tag in ("td", "th") and self._cell is not None and self._row is not None:
            self._row.append(" ".join(self._cell).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None and self._table is not None:
            if any(c.strip() for c in self._row):
                self._table.append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            if self._table:
                self.tables.append(self._table)
            self._table = None

    def handle_data(self, data):
        if self._cell is not None:
            text = data.strip()
            if text:
                self._cell.append(text)

    def handle_entityref(self, name):
        if self._cell is not None:
            self._cell.append({"amp": "&", "lt": "<", "gt": ">", "nbsp": " "}.get(name, ""))

    def handle_charref(self, name):
        if self._cell is not None:
            try:
                ch = chr(int(name[1:], 16) if name.startswith('x') else int(name))
                self._cell.append(ch)
            except (ValueError, OverflowError):
                pass


def _col_index(header: list[str], keywords: list[str]) -> int:
    """Return the first column index whose text contains any of the keywords."""
    for i, cell in enumerate(header):
        if any(kw in cell for kw in keywords):
            return i
    return -1


def _parse_pairzon_html(html: str) -> list:
    """
    Extract product rows from a Pairzon receipt HTML page.

    Finds a <table> whose first row contains recognisable column headers
    (תאור / שם מוצר for name, and סופק for supplied qty), then returns
    rows where qty_supplied > 0.
    """
    parser = _TableParser()
    parser.feed(html)

    NAME_KEYWORDS    = ["תאור", "שם מוצר", "שם הפריט"]
    SUPPLIED_KEYWORDS = ["סופק", "כמות שסופקה", "סופקה"]

    for table in parser.tables:
        if len(table) < 2:
            continue

        header = table[0]
        name_col     = _col_index(header, NAME_KEYWORDS)
        supplied_col = _col_index(header, SUPPLIED_KEYWORDS)

        if name_col == -1 or supplied_col == -1:
            continue  # not the receipt table

        items = []
        for row in table[1:]:
            if len(row) <= max(name_col, supplied_col):
                continue
            name = row[name_col].strip()
            supplied_str = row[supplied_col].strip()
            if not name or not supplied_str:
                continue
            try:
                qty = float(supplied_str.replace(',', '.'))
            except ValueError:
                continue
            if qty <= 0:
                continue
            items.append({"name": name, "qty": qty})

        if items:
            return items  # found the receipt table

    return []


def parse_pairzon_url(url: str) -> list:
    """
    Fetch a live Pairzon HTML receipt and extract purchased items.

    Sends a browser-like User-Agent so the server returns a full page.
    Returns list of {"name": str, "qty": float} for items where qty > 0.
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0 Safari/537.36"
        ),
        "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    }
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        # Ensure Hebrew is decoded correctly
        resp.encoding = resp.apparent_encoding or "utf-8"
        html = resp.text
    except requests.RequestException as e:
        print(f"[receipt_parser] Failed to fetch Pairzon URL {url!r}: {e}")
        return []

    items = _parse_pairzon_html(html)
    print(f"[receipt_parser] Pairzon HTML: {len(items)} product lines")
    return items


# ── Batch LLM normalisation ────────────────────────────────────────────────────

_NORMALIZE_BATCH_PROMPT = """\
אתה עוזר בית. נרמל את שמות המוצרים הבאים לעברית תקינה ותמציתית.
הסר פרטים כמו משקל, נפח, כמות, מידה (ל', קג, גרם, מ"ל, יחידות, XL וכד').
שמור על שם המוצר הבסיסי ועל אחוז שומן אם רלוונטי (כגון 3%).
אל תוסיף מידע שאינו בשם המקורי.

שמות לנרמול:
{numbered_list}

החזר בדיוק {count} שורות ממוספרות (ספרה + נקודה + שם בלבד):"""


def normalize_item_names(names: list) -> list:
    """
    Normalize a batch of Hebrew product names via a single LLM call.

    Checks the translation cache first; only calls the LLM for names not
    already cached.  Falls back to the original names on LLM error.
    Returns a list of normalised names in the same order as input.
    """
    if not names:
        return []

    cache = _load_cache()
    cached = [cache.get(n) for n in names]

    uncached_indices = [i for i, v in enumerate(cached) if v is None]
    if not uncached_indices:
        return [v for v in cached]  # type: ignore[return-value]

    uncached_names = [names[i] for i in uncached_indices]
    numbered = "\n".join(f"{i+1}. {n}" for i, n in enumerate(uncached_names))
    prompt = _NORMALIZE_BATCH_PROMPT.format(
        numbered_list=numbered, count=len(uncached_names)
    )

    llm_results = list(uncached_names)  # default: keep originals
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model": OLLAMA_MODEL,
                "prompt": prompt,
                "stream": False,
                "options": {"temperature": 0.1, "num_predict": len(uncached_names) * 25},
            },
            timeout=30,
        )
        resp.raise_for_status()
        llm_text = resp.json().get("response", "").strip()

        for line in llm_text.splitlines():
            m = re.match(r'^(\d+)[.)]\s*(.+)$', line.strip())
            if m:
                idx = int(m.group(1)) - 1
                norm = m.group(2).strip()
                if 0 <= idx < len(llm_results) and norm:
                    llm_results[idx] = norm
                    # Cache only if LLM changed the name
                    if norm != uncached_names[idx]:
                        save_translation(uncached_names[idx], norm)
    except requests.RequestException as e:
        print(f"[receipt_parser] Ollama batch normalize error: {e}")

    # Merge cached + LLM results
    result = list(names)
    for i, llm_idx in enumerate(uncached_indices):
        result[llm_idx] = llm_results[i]
    for i, cached_val in enumerate(cached):
        if cached_val is not None:
            result[i] = cached_val

    return result


# ── Per-item LLM normalisation (Hazi Hinam) ───────────────────────────────────

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
    Normalize a single Hazi Hinam product name (reversed Hebrew) to clean Hebrew.

    Returns (normalized_name, is_certain).
    """
    cache = _load_cache()
    if raw_name in cache:
        return cache[raw_name], True

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
        result = result.splitlines()[0].strip() if result else ""
    except requests.RequestException as e:
        print(f"[receipt_parser] Ollama error normalizing '{raw_name}': {e}")
        return raw_name, False

    if not result or result == raw_name:
        return raw_name, False

    save_translation(raw_name, result)
    return result, True


# ── Public API ────────────────────────────────────────────────────────────────

def import_receipt(pdf_bytes: bytes) -> tuple:
    """
    Parse a receipt PDF and classify each product.

    Detects the format automatically and routes to the appropriate parser:
      "shufersal"  → parse_shufersal() + normalize_item_names() (batch LLM)
      "hazi_hinam" → parse_receipt_lines() + normalize_product_name() (per-item)
      other        → empty result

    Returns (certain, uncertain):
      certain   — list of clean Hebrew names ready for db.set_inventory_status
      uncertain — list of raw strings needing human confirmation (Hazi Hinam only)
    """
    text = extract_text_from_pdf(pdf_bytes)
    fmt = detect_format(text)
    print(f"[receipt_parser] Detected format: {fmt}")

    if fmt == "shufersal":
        items = parse_shufersal(text)
        print(f"[receipt_parser] Shufersal: {len(items)} product lines")
        if not items:
            return [], []
        raw_names = [item["name"] for item in items]
        normalized = normalize_item_names(raw_names)
        return normalized, []

    if fmt == "hazi_hinam":
        raw_names = parse_receipt_lines(text)
        print(f"[receipt_parser] Hazi Hinam: {len(raw_names)} product lines")
        certain, uncertain = [], []
        for raw in raw_names:
            name, is_certain = normalize_product_name(raw)
            print(f"[receipt_parser]   '{raw}' → '{name}' (certain={is_certain})")
            if is_certain:
                certain.append(name)
            else:
                uncertain.append(raw)
        return certain, uncertain

    print(f"[receipt_parser] Unknown format — no items extracted")
    return [], []
