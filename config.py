# config.py — runtime configuration
# Copy and edit this file to match your environment.

import os

# ── Telegram ───────────────────────────────────────────────────────────────────
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "YOUR_TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")   # used by scheduler

# ── Database ───────────────────────────────────────────────────────────────────
DB_PATH = os.getenv("DB_PATH", "data/grocery.db")

# ── Ollama (local LLM) ─────────────────────────────────────────────────────────
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "dictalm2.0-instruct")

# ── Intent parser ──────────────────────────────────────────────────────────────
VALID_ACTIONS = {"add", "remove", "depleted", "low", "list", "inventory", "recipe", "unknown"}

# ── OCR ────────────────────────────────────────────────────────────────────────
TESSERACT_LANG = "heb+eng"
URL_TEXT_MAX_CHARS = 3000

# ── Scheduler ─────────────────────────────────────────────────────────────────
DEPLETION_THRESHOLD_DAYS = 3   # warn if predicted to run out within N days
USAGE_LOOKBACK_DAYS = 90       # how far back to look for usage patterns
