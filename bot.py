# bot.py — Telegram bot entry point

import logging
from telegram import Update
from telegram.ext import (
    ApplicationBuilder, CommandHandler,
    MessageHandler, filters, ContextTypes
)

import db
import executor
import intent_parser
import list_flow
import recipe_flow
import recipe_parser
import receipt_parser
import receipt_flow
from config import TELEGRAM_TOKEN

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)


# ── Standard commands ─────────────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "שלום! אני עוזר הקניות שלך 🛒\n\n"
        "פשוט כתוב לי בעברית רגילה, למשל:\n"
        "  • *קניתי חלב וביצים*\n"
        "  • *נגמר הלחם*\n"
        "  • *מה צריך לקנות?*\n"
        "  • *מה יש בבית?*\n"
        "  • *מה אפשר לבשל?*\n\n"
        "פקודות: /help",
        parse_mode="Markdown"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📋 *פקודות זמינות:*\n\n"
        "*קניות ומלאי:*\n"
        "  /list — רשימת קניות\n"
        "  /inventory — מלאי הבית\n"
        "  /clear — נקה רשימת קניות\n\n"
        "*מתכונים:*\n"
        "  /add\\_recipe — הוסף מתכון (טקסט / תמונה / קישור)\n"
        "  /step — הוסף מתכון שלב-שלב\n"
        "  /recipes — כל המתכונים\n"
        "  /recipe <שם> — פרטי מתכון\n"
        "  /add\\_recipe\\_to\\_list <שם> — הוסף מרכיבים חסרים לרשימה\n"
        "  /del\\_recipe <שם> — מחק מתכון\n"
        "  /cancel — בטל פעולה נוכחית\n\n"
        "*רשימות שמורות:*\n"
        "  /save\\_list <שם> — שמור רשימת קניות בשם\n"
        "  /lists — כל הרשימות השמורות\n"
        "  /use\\_list <שם> — עבור על רשימה והוסף פריטים\n"
        "  /del\\_list <שם> — מחק רשימה שמורה\n\n"
        "*שפה חופשית:*\n"
        "  קניתי / נגמר / מה יש / מה לבשל / הוסף מתכון...\n\n"
        "*ייבוא חשבונית:*\n"
        "  /import — ייבא חשבונית PDF של חצי חינם",
        parse_mode="Markdown"
    )


async def cmd_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(executor.handle({"action": "list", "items": []}))


async def cmd_inventory(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(executor.handle({"action": "inventory", "items": []}))


async def cmd_clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db.clear_shopping_list()
    await update.message.reply_text("✅ רשימת הקניות נוקתה.")


async def cmd_failed(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rows = db.get_failed_parses(10)
    if not rows:
        await update.message.reply_text("אין הודעות שלא הובנו עד כה.")
        return
    lines = [f"⚠️ *10 הודעות אחרונות שלא הובנו:*\n"]
    for r in rows:
        lines.append(
            f"🕐 {r['logged_at']}\n"
            f"  משתמש: {r['user_message']}\n"
            f"  LLM: {r['raw_llm_response'][:120]}\n"
        )
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_save_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args) if context.args else ""
    if not name:
        await update.message.reply_text("שימוש: /save\\_list <שם הרשימה>", parse_mode="Markdown")
        return
    chat_id = update.effective_chat.id
    reply = list_flow.start_save(chat_id, name)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_lists(update: Update, context: ContextTypes.DEFAULT_TYPE):
    names = db.get_all_named_lists()
    if not names:
        await update.message.reply_text(
            "אין רשימות שמורות עדיין.\nהוסף עם /save\\_list <שם>",
            parse_mode="Markdown"
        )
        return
    lines = ["📋 *הרשימות השמורות שלך:*\n"]
    for i, name in enumerate(names, 1):
        lines.append(f"  {i}. {name}")
    lines.append("\nלשימוש: /use\\_list <שם>")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_use_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args) if context.args else ""
    if not name:
        await update.message.reply_text("שימוש: /use\\_list <שם הרשימה>", parse_mode="Markdown")
        return
    reply = list_flow.start_use(update.effective_chat.id, name)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_del_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args) if context.args else ""
    if not name:
        await update.message.reply_text("שימוש: /del\\_list <שם הרשימה>", parse_mode="Markdown")
        return
    if db.delete_named_list(name):
        await update.message.reply_text(f"🗑️ הרשימה *{name}* נמחקה.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"לא מצאתי רשימה בשם '{name}'.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    if recipe_flow.is_active(chat_id):
        recipe_flow._clear(chat_id)
        await update.message.reply_text("בוטל ✅")
    elif list_flow.is_active(chat_id):
        list_flow.cancel(chat_id)
        await update.message.reply_text("בוטל ✅")
    elif receipt_flow.is_active(chat_id):
        receipt_flow.cancel(chat_id)
        await update.message.reply_text("בוטל ✅")
    else:
        await update.message.reply_text("אין פעולה פעילה לביטול.")


# ── Recipe commands ───────────────────────────────────────────────────────────

async def cmd_add_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    reply = recipe_flow.start(chat_id)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_step(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    reply = recipe_flow.start_step_by_step(chat_id)
    await update.message.reply_text(reply, parse_mode="Markdown")


async def cmd_recipes(update: Update, context: ContextTypes.DEFAULT_TYPE):
    recipes = db.get_all_recipes()
    if not recipes:
        await update.message.reply_text(
            "אין מתכונים שמורים עדיין.\n"
            "הוסף מתכון עם /add\\_recipe",
            parse_mode="Markdown"
        )
        return
    lines = ["📖 *המתכונים שלך:*\n"]
    for i, r in enumerate(recipes, 1):
        lines.append(f"  {i}\\. {r['name']} \\({r['servings']} מנות\\)")
    lines.append("\nלפרטים: /recipe <שם>")
    await update.message.reply_text("\n".join(lines), parse_mode="MarkdownV2")


async def cmd_recipe_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args) if context.args else ""
    if not name:
        await update.message.reply_text("שימוש: /recipe <שם המתכון>")
        return
    recipe = db.get_recipe_by_name(name)
    if not recipe:
        await update.message.reply_text(f"לא מצאתי מתכון בשם '{name}'.")
        return
    await update.message.reply_text(
        recipe_parser.format_recipe_full(recipe),
        parse_mode="Markdown"
    )


async def cmd_del_recipe(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args) if context.args else ""
    if not name:
        await update.message.reply_text("שימוש: /del\\_recipe <שם המתכון>", parse_mode="Markdown")
        return
    deleted = db.delete_recipe(name)
    if deleted:
        await update.message.reply_text(f"🗑️ המתכון *{name}* נמחק.", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"לא מצאתי מתכון בשם '{name}'.")


async def cmd_add_recipe_to_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = " ".join(context.args) if context.args else ""
    if not name:
        await update.message.reply_text("שימוש: /add\\_recipe\\_to\\_list <שם המתכון>", parse_mode="Markdown")
        return
    recipe = db.get_recipe_by_name(name)
    if not recipe:
        await update.message.reply_text(f"לא מצאתי מתכון בשם '{name}'.")
        return
    missing = db.get_recipe_missing_ingredients(recipe["id"])
    if not missing:
        await update.message.reply_text(
            f"✅ כל המרכיבים של *{recipe['name']}* כבר יש בבית!",
            parse_mode="Markdown"
        )
        return
    for item in missing:
        db.add_to_shopping_list(item)
    lines = "\n".join(f"  • {item}" for item in missing)
    await update.message.reply_text(
        f"🛒 הוספתי לרשימת הקניות ({len(missing)} פריטים חסרים מ*{recipe['name']}*):\n{lines}",
        parse_mode="Markdown"
    )


# ── Message handler ───────────────────────────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    user_text = (update.message.text or "").strip()

    # If in a recipe flow, route there first
    if recipe_flow.is_active(chat_id):
        reply = recipe_flow.handle(chat_id, text=user_text)
        if reply:
            await update.message.reply_text(reply, parse_mode="Markdown")
        return

    # If in a list flow, route there
    if list_flow.is_active(chat_id):
        reply = list_flow.handle(chat_id, user_text)
        if reply:
            await update.message.reply_text(reply, parse_mode="Markdown")
        return

    # If confirming uncertain products from a receipt import, route there
    if receipt_flow.is_active(chat_id):
        reply = receipt_flow.handle(chat_id, user_text)
        if reply:
            await update.message.reply_text(reply, parse_mode="Markdown")
        return

    if not user_text:
        return

    logger.info(f"Message from {chat_id}: {repr(user_text)}")
    await context.bot.send_chat_action(chat_id=chat_id, action="typing")

    parsed = intent_parser.parse_intent(user_text)

    # "recipe" action from intent parser = user wants to ADD a recipe
    if parsed["action"] == "recipe" and not parsed["items"]:
        # Check if they're asking what to cook vs wanting to add a recipe
        add_triggers = {"הוסף", "תוסיף", "שמור", "חדש", "רשום", "הכנס"}
        if any(t in user_text for t in add_triggers):
            reply = recipe_flow.start(chat_id)
            await update.message.reply_text(reply, parse_mode="Markdown")
            return

    reply = executor.handle(parsed)
    await update.message.reply_text(reply)


async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming photos — recipe OCR if in recipe flow, prompt otherwise."""
    chat_id = update.effective_chat.id

    if recipe_flow.is_active(chat_id):
        await context.bot.send_chat_action(chat_id=chat_id, action="typing")
        photo = update.message.photo[-1]  # highest resolution
        tg_file = await context.bot.get_file(photo.file_id)
        image_bytes = bytes(await tg_file.download_as_bytearray())
        reply = recipe_flow.handle(chat_id, image_bytes=image_bytes)
        if reply:
            await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        await update.message.reply_text(
            "קיבלתי תמונה 📷\n\n"
            "אם זה מתכון, כתוב /add\\_recipe ואז שלח את התמונה.",
            parse_mode="Markdown"
        )


# ── Receipt import ────────────────────────────────────────────────────────────

async def cmd_import(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt the user to send a Hazi Hinam PDF receipt for import."""
    await update.message.reply_text(
        "📄 שלח לי את קובץ ה-PDF של החשבונית (פורמט חצי חינם).\n"
        "אחלץ ממנו את הפריטים שנרכשו ואעדכן את המלאי."
    )


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle document uploads — treat PDFs as Hazi Hinam receipt imports."""
    doc = update.message.document
    if not doc or doc.mime_type != "application/pdf":
        await update.message.reply_text("אני יודע לעבד רק קבצי PDF כרגע.")
        return

    await update.message.reply_text("📄 מעבד חשבונית... זה עשוי לקחת כמה שניות.")
    await context.bot.send_chat_action(chat_id=update.effective_chat.id, action="typing")

    tg_file = await context.bot.get_file(doc.file_id)
    pdf_bytes = bytes(await tg_file.download_as_bytearray())

    try:
        certain, uncertain = receipt_parser.import_receipt(pdf_bytes)
    except Exception as e:
        logger.error(f"Receipt import error: {e}")
        await update.message.reply_text(f"❌ אירעה שגיאה בעיבוד החשבונית: {e}")
        return

    if not certain and not uncertain:
        await update.message.reply_text(
            "⚠️ לא מצאתי פריטים בחשבונית.\n"
            "ודא שזהו קובץ חשבונית של חצי חינם בפורמט הנכון."
        )
        return

    # Update inventory for all confidently-normalized items
    for item in certain:
        db.set_inventory_status(item, "יש")
        db.remove_from_shopping_list(item)

    if certain:
        lines = "\n".join(f"  ✅ {item}" for item in certain)
        await update.message.reply_text(
            f"🛒 עדכנתי *{len(certain)} פריטים* במלאי:\n{lines}",
            parse_mode="Markdown"
        )

    # If some items couldn't be normalized, start the confirmation conversation
    if uncertain:
        reply = receipt_flow.start(update.effective_chat.id, uncertain)
        await update.message.reply_text(reply, parse_mode="Markdown")


# ── Startup ───────────────────────────────────────────────────────────────────

def main():
    db.init_db()
    logger.info("Database initialized.")

    if TELEGRAM_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        logger.error("TELEGRAM_TOKEN not set in config.py")
        return

    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start",      cmd_start))
    app.add_handler(CommandHandler("help",       cmd_help))
    app.add_handler(CommandHandler("list",       cmd_list))
    app.add_handler(CommandHandler("inventory",  cmd_inventory))
    app.add_handler(CommandHandler("clear",      cmd_clear))
    app.add_handler(CommandHandler("cancel",     cmd_cancel))
    app.add_handler(CommandHandler("add_recipe", cmd_add_recipe))
    app.add_handler(CommandHandler("step",       cmd_step))
    app.add_handler(CommandHandler("recipes",    cmd_recipes))
    app.add_handler(CommandHandler("recipe",     cmd_recipe_detail))
    app.add_handler(CommandHandler("del_recipe",          cmd_del_recipe))
    app.add_handler(CommandHandler("add_recipe_to_list", cmd_add_recipe_to_list))
    app.add_handler(CommandHandler("save_list",          cmd_save_list))
    app.add_handler(CommandHandler("lists",              cmd_lists))
    app.add_handler(CommandHandler("use_list",           cmd_use_list))
    app.add_handler(CommandHandler("del_list",           cmd_del_list))
    app.add_handler(CommandHandler("failed",             cmd_failed))
    app.add_handler(CommandHandler("import",             cmd_import))
    app.add_handler(MessageHandler(filters.Document.PDF, handle_document))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Bot running. Press Ctrl+C to stop.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
