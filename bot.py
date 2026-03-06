# bot.py — Telegram bot entry point
#
# Run with: python bot.py
# Uses python-telegram-bot v20 (async)

import logging
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes

import db
import executor
import intent_parser
from config import TELEGRAM_TOKEN

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ── Command handlers ──────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "שלום! אני עוזר הקניות שלך 🛒\n\n"
        "פשוט כתוב לי בעברית רגילה, למשל:\n"
        "  • *קניתי חלב וביצים*\n"
        "  • *נגמר הלחם*\n"
        "  • *מה צריך לקנות?*\n"
        "  • *מה יש בבית?*\n"
        "  • *מה אפשר לבשל?*\n\n"
        "פקודות נוספות: /help",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *מה אני יכול לעשות:*\n\n"
        "*עדכון מלאי:*\n"
        "  קניתי חלב וביצים\n"
        "  יש בבית עגבניות\n\n"
        "*כשנגמר משהו:*\n"
        "  נגמר החלב\n"
        "  אין לחם\n"
        "  הביצים על הסף\n\n"
        "*צפייה:*\n"
        "  מה צריך לקנות?\n"
        "  מה יש בבית?\n\n"
        "*בישול:*\n"
        "  מה אפשר לבשל?\n"
        "  הצע מתכון\n\n"
        "פקודות ישירות:\n"
        "  /list — רשימת קניות\n"
        "  /inventory — מלאי\n"
        "  /clear — נקה רשימת קניות",
        parse_mode="Markdown"
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = executor.handle({"action": "list", "items": []})
    await update.message.reply_text(reply)


async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reply = executor.handle({"action": "inventory", "items": []})
    await update.message.reply_text(reply)


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.clear_shopping_list()
    await update.message.reply_text("✅ רשימת הקניות נוקתה.")


# ── Message handler (main flow) ───────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text.strip()

    if not user_text:
        return

    logger.info(f"Received: {repr(user_text)}")

    # Send typing indicator while LLM processes
    await context.bot.send_chat_action(
        chat_id=update.effective_chat.id,
        action="typing"
    )

    # Parse intent via LLM
    parsed = intent_parser.parse_intent(user_text)

    # Execute action and get Hebrew reply
    reply = executor.handle(parsed)

    await update.message.reply_text(reply)


# ── Startup ───────────────────────────────────────────────────────────────────

def main():
    # Initialize database on startup
    db.init_db()
    logger.info("Database initialized.")

    if TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error(
            "TELEGRAM_TOKEN not set! Edit config.py and add your bot token from @BotFather"
        )
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Register handlers
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("inventory", cmd_inventory))
    app.add_handler(CommandHandler("clear", cmd_clear))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot started. Waiting for messages...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
