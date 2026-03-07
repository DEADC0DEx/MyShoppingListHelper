# expense_bot/bot.py — Telegram entry point

import logging
from datetime import date

from telegram import (
    Update, InlineKeyboardMarkup, Document
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, filters, ContextTypes,
)

import db
import executor
import parser as expense_parser
import flow
from config import TELEGRAM_TOKEN, CATEGORIES

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Commands ───────────────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "שלום! אני עוזר ניהול ההוצאות שלך 💳\n\n"
        "מה אפשר לעשות:\n"
        "  • כתוב הוצאה: *שילמתי 150 ₪ ברמי לוי*\n"
        "  • ייבא חשבון: /import\\_statement\n"
        "  • שלח קובץ CSV ישירות לצ'אט\n"
        "  • ראה הוצאות החודש: /expenses\n"
        "  • סיכום לפי קטגוריות: /summary\n\n"
        "פקודות: /help",
        parse_mode="Markdown",
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cat_list = "\n".join(f"    • {c}" for c in CATEGORIES)
    await update.message.reply_text(
        "💳 *פקודות זמינות:*\n\n"
        "*הוצאות:*\n"
        "  /expenses — הוצאות החודש הנוכחי\n"
        "  /expenses MM YYYY — הוצאות לחודש ספציפי\n"
        "  /summary — סיכום לפי קטגוריות (חודש נוכחי)\n"
        "  /summary MM YYYY — סיכום לחודש ספציפי\n\n"
        "*ייבוא:*\n"
        "  /import\\_statement — הדבק טקסט חשבון אשראי\n"
        "  שלח קובץ CSV — ייבוא אוטומטי\n\n"
        "*ניהול:*\n"
        "  /merchants — כל בתי העסק הידועים\n"
        "  /set\\_category <עסק> | <קטגוריה> — שנה קטגוריה לבית עסק\n"
        "  /categories — רשימת קטגוריות\n"
        "  /cancel — בטל פעולה נוכחית\n\n"
        f"*קטגוריות:*\n{cat_list}",
        parse_mode="Markdown",
    )


async def cmd_expenses(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    try:
        m = int(context.args[0]) if context.args else today.month
        y = int(context.args[1]) if len(context.args) > 1 else today.year
    except (ValueError, IndexError):
        m, y = today.month, today.year
    await update.message.reply_text(executor.handle_view(y, m), parse_mode="Markdown")


async def cmd_summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    today = date.today()
    try:
        m = int(context.args[0]) if context.args else today.month
        y = int(context.args[1]) if len(context.args) > 1 else today.year
    except (ValueError, IndexError):
        m, y = today.month, today.year
    await update.message.reply_text(executor.handle_summary(y, m), parse_mode="Markdown")


async def cmd_import_statement(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    reply = flow.start_statement_import(chat_id)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_merchants(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(executor.handle_merchants(), parse_mode="Markdown")


async def cmd_categories(update: Update, context: ContextTypes.DEFAULT_TYPE):
    lines = ["📂 *קטגוריות:*\n"]
    for i, c in enumerate(CATEGORIES, 1):
        lines.append(f"  {i}. {c}")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_set_category(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Usage: /set_category <merchant> | <category>"""
    raw = " ".join(context.args) if context.args else ""
    if "|" not in raw:
        await update.message.reply_text(
            "שימוש: /set\\_category <בית עסק> | <קטגוריה>\n"
            "דוגמה: /set\\_category רמי לוי | אוכל וסופרמרקט",
            parse_mode="Markdown",
        )
        return
    parts = raw.split("|", 1)
    merchant = parts[0].strip()
    category = parts[1].strip()
    await update.message.reply_text(
        executor.handle_set_category(merchant, category),
        parse_mode="Markdown",
    )


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if flow.is_active(chat_id):
        flow.cancel(chat_id)
        await update.message.reply_text("בוטל ✅")
    else:
        await update.message.reply_text("אין פעולה פעילה לביטול.")


# ── Message handlers ───────────────────────────────────────────────────────────

async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = (update.message.text or "").strip()
    if not user_text:
        return

    # Route to active flow first
    if flow.is_active(chat_id):
        mode = flow.current_mode(chat_id)
        if mode == "awaiting_statement":
            await context.bot.send_chat_action(chat_id=chat_id, action="typing")
            reply = flow.handle_text(chat_id, user_text)
            # After parsing, may have switched to "reviewing"
            if flow.is_active(chat_id) and flow.current_mode(chat_id) == "reviewing":
                await update.message.reply_text(
                    reply,
                    parse_mode="Markdown",
                    reply_markup=InlineKeyboardMarkup(flow.build_category_keyboard()),
                )
            else:
                await update.message.reply_text(reply, parse_mode="Markdown")
            return
        # reviewing mode — user should be using inline buttons, not text
        await update.message.reply_text(
            "בבקשה בחר קטגוריה מהכפתורים למטה, או /cancel לביטול."
        )
        return

    logger.info(f"Message from {chat_id}: {repr(user_text)}")
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    action = expense_parser.parse_intent(user_text)

    if action == "add_expense":
        known = {m["display"]: m["category"] for m in db.get_all_merchants()}
        expense = expense_parser.parse_single_expense(user_text, known)
        if not expense:
            await update.message.reply_text(
                "לא הצלחתי להבין את ההוצאה.\n"
                "נסה: *שילמתי 150 ₪ ברמי לוי*",
                parse_mode="Markdown",
            )
            return
        # Check if merchant is new
        stored_cat = db.get_merchant_category(expense["merchant"])
        if stored_cat:
            expense["category"] = stored_cat
            reply = executor.handle_add(expense)
            await update.message.reply_text(reply, parse_mode="Markdown")
        else:
            # New merchant — confirm category inline
            reply = (
                f"💳 הוצאה: *{expense['merchant']}*  {executor.format_amount(expense['amount'], expense.get('currency','ILS'))}\n"
                f"קטגוריה מוצעת: _{expense['category']}_\n\n"
                "אשר קטגוריה:"
            )
            # Temporarily stash in flow state as single-expense review
            flow._state[chat_id] = {
                "mode": "reviewing",
                "pending_expenses": [expense],
                "new_merchants": [expense["merchant"]],
                "review_idx": 0,
                "source": "manual",
            }
            await update.message.reply_text(
                reply,
                parse_mode="Markdown",
                reply_markup=InlineKeyboardMarkup(flow.build_category_keyboard()),
            )

    elif action == "import_statement":
        reply = flow.start_statement_import(chat_id)
        await update.message.reply_text(reply, parse_mode="Markdown")

    elif action == "view_expenses":
        today = date.today()
        await update.message.reply_text(
            executor.handle_view(today.year, today.month), parse_mode="Markdown"
        )

    elif action == "view_summary":
        today = date.today()
        await update.message.reply_text(
            executor.handle_summary(today.year, today.month), parse_mode="Markdown"
        )

    else:
        await update.message.reply_text(
            "לא הבנתי. נסה:\n"
            "  • *שילמתי 150 ₪ ברמי לוי*\n"
            "  • /import\\_statement — ייבוא חשבון\n"
            "  • /expenses — הוצאות החודש\n"
            "  • /help — כל הפקודות",
            parse_mode="Markdown",
        )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle uploaded CSV files."""
    chat_id = update.effective_chat.id
    doc: Document = update.message.document
    fname = doc.file_name or ""

    if not (fname.lower().endswith(".csv") or fname.lower().endswith(".txt")):
        await update.message.reply_text(
            "תומך בקבצי CSV בלבד.\n"
            "לייבוא טקסט חשבון: /import\\_statement",
            parse_mode="Markdown",
        )
        return

    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    # Mark state so handle_csv knows the source
    flow._state[chat_id] = {"mode": "awaiting_csv", "source": "csv"}

    tg_file = await context.bot.get_file(doc.file_id)
    file_bytes = bytes(await tg_file.download_as_bytearray())
    reply = flow.handle_csv(chat_id, file_bytes)

    if flow.is_active(chat_id) and flow.current_mode(chat_id) == "reviewing":
        await update.message.reply_text(
            reply,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(flow.build_category_keyboard()),
        )
    else:
        await update.message.reply_text(reply, parse_mode="Markdown")


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses for category selection."""
    query = update.callback_query
    await query.answer()

    chat_id = update.effective_chat.id
    data = query.data

    if not flow.is_active(chat_id):
        await query.edit_message_text("הפעולה פגה תוקף. שלח הוצאה חדשה.")
        return

    reply_text, done = flow.handle_callback(chat_id, data)

    if done:
        await query.edit_message_text(reply_text, parse_mode="Markdown")
    else:
        await query.edit_message_text(
            reply_text,
            parse_mode="Markdown",
            reply_markup=InlineKeyboardMarkup(flow.build_category_keyboard()),
        )


# ── Startup ────────────────────────────────────────────────────────────────────

def main():
    db.init_db()
    logger.info("Expense DB initialized.")

    if TELEGRAM_TOKEN == "YOUR_EXPENSE_BOT_TOKEN":
        logger.error("Set TELEGRAM_TOKEN in expense_bot/config.py")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",            cmd_start))
    app.add_handler(CommandHandler("help",             cmd_help))
    app.add_handler(CommandHandler("expenses",         cmd_expenses))
    app.add_handler(CommandHandler("summary",          cmd_summary))
    app.add_handler(CommandHandler("import_statement", cmd_import_statement))
    app.add_handler(CommandHandler("merchants",        cmd_merchants))
    app.add_handler(CommandHandler("categories",       cmd_categories))
    app.add_handler(CommandHandler("set_category",     cmd_set_category))
    app.add_handler(CommandHandler("cancel",           cmd_cancel))

    app.add_handler(CallbackQueryHandler(handle_callback))
    app.add_handler(MessageHandler(filters.Document.ALL, handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    logger.info("Expense bot running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
