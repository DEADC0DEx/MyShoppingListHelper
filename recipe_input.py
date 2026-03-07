# recipe_input.py — extract raw text from different input sources
#
# Three sources:
#   image_to_text(image_bytes) → str   (OCR via pytesseract)
#   url_to_text(url)           → str   (HTTP fetch + HTML strip)
#   text_is_url(text)          → bool  (quick check)

import re
import urllib.request
from html.parser import HTMLParser
from config import TESSERACT_LANG, URL_TEXT_MAX_CHARS


# ── URL detection ─────────────────────────────────────────────────────────────

def text_is_url(text: str) -> bool:
    """Return True if the text looks like a URL."""
    text = text.strip()
    return text.startswith("http://") or text.startswith("https://")


# ── Image → text (OCR) ────────────────────────────────────────────────────────

def image_to_text(image_bytes: bytes) -> str:
    """
    Extract text from image bytes using Tesseract OCR.
    Requires: sudo apt install tesseract-ocr tesseract-ocr-heb
              pip install pytesseract pillow
    """
    try:
        import pytesseract
        from PIL import Image
        import io

        image = Image.open(io.BytesIO(image_bytes))
        text = pytesseract.image_to_string(image, lang=TESSERACT_LANG)
        text = text.strip()

        if not text:
            return ""

        print(f"[recipe_input] OCR extracted {len(text)} chars")
        return text

    except ImportError:
        raise RuntimeError(
            "pytesseract או pillow לא מותקנים.\n"
            "הרץ: pip install pytesseract pillow\n"
            "ו: sudo apt install tesseract-ocr tesseract-ocr-heb"
        )
    except Exception as e:
        print(f"[recipe_input] OCR error: {e}")
        raise RuntimeError(f"שגיאה בקריאת התמונה: {e}")


# ── URL → text ────────────────────────────────────────────────────────────────

class _TextExtractor(HTMLParser):
    """Minimal HTML parser that extracts visible text, skipping scripts/styles."""

    SKIP_TAGS = {"script", "style", "noscript", "head", "nav", "footer", "header"}

    def __init__(self):
        super().__init__()
        self._skip = 0
        self.chunks = []

    def handle_starttag(self, tag, attrs):
        if tag.lower() in self.SKIP_TAGS:
            self._skip += 1

    def handle_endtag(self, tag):
        if tag.lower() in self.SKIP_TAGS:
            self._skip = max(0, self._skip - 1)

    def handle_data(self, data):
        if self._skip == 0:
            text = data.strip()
            if text:
                self.chunks.append(text)

    def get_text(self) -> str:
        return "\n".join(self.chunks)


def url_to_text(url: str) -> str:
    """
    Fetch a URL and return its visible text content.
    Strips HTML tags, scripts, and navigation elements.
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (compatible; GroceryBot/1.0)"}
        )
        with urllib.request.urlopen(req, timeout=15) as response:
            # Detect encoding
            content_type = response.headers.get("Content-Type", "")
            charset = "utf-8"
            if "charset=" in content_type:
                charset = content_type.split("charset=")[-1].strip()

            raw_html = response.read().decode(charset, errors="replace")

    except Exception as e:
        print(f"[recipe_input] URL fetch error: {e}")
        raise RuntimeError(f"לא הצלחתי לטעון את הכתובת: {e}")

    parser = _TextExtractor()
    try:
        parser.feed(raw_html)
    except Exception:
        pass  # partial parse is fine

    text = parser.get_text()

    # Collapse excessive whitespace
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r" {2,}", " ", text)

    # Truncate to avoid overwhelming the LLM
    if len(text) > URL_TEXT_MAX_CHARS:
        text = text[:URL_TEXT_MAX_CHARS] + "\n[...]"

    print(f"[recipe_input] URL extracted {len(text)} chars from {url}")
    return text.strip()
