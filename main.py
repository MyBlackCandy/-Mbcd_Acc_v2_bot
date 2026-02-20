import os
import re
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
from database import get_db_connection, init_db

TOKEN = os.getenv("TOKEN")
MASTER_ID = os.getenv("MASTER_ID")

if not TOKEN or not MASTER_ID:
    raise ValueError("TOKEN or MASTER_ID not set")

# ==============================
# 🌍 3 LANGUAGE SYSTEM
# ==============================

LANG_TEXT = {
    "zh": {
        "system_ok": "系统运行正常 ✅",
        "no_record": "今天没有记录",
        "help": "使用 /start /check /showall /undo /reset /add /addlist /resetadd /timezone /settime /renew /lang",
    },
    "th": {
        "system_ok": "ระบบทำงานปกติ ✅",
        "no_record": "วันนี้ไม่มีรายการ",
        "help": "ใช้คำสั่ง /start /check /showall /undo /reset /add /addlist /resetadd /timezone /settime /renew /lang",
    },
    "en": {
        "system_ok": "System running normally ✅",
        "no_record": "No records today",
        "help": "Use /start /check /showall /undo /reset /add /addlist /resetadd /timezone /settime /renew /lang",
    }
}


def get_lang(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT language FROM chat_settings WHERE chat_id=%s", (chat_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row[0] if row else "zh"


def t(chat_id, key):
    return LANG_TEXT.get(get_lang(chat_id), LANG_TEXT["zh"])[key]


# ==============================
# 🔐 ROLE SYSTEM
# ==============================

async def get_role(update: Update):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id

    if str(user_id) == str(MASTER_ID):
        return "master"

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT expire_date FROM owners WHERE user_id=%s", (user_id,))
    row = cursor.fetchone()
    if row and row[0] > datetime.utcnow():
        cursor.close(); conn.close()
        return "owner"

    cursor.execute("""
        SELECT role FROM members
        WHERE user_id=%s AND chat_id=%s
    """, (user_id, chat_id))
    row = cursor.fetchone()

    cursor.close()
    conn.close()

    return row[0] if row else "normal"


# ==============================
# 🚀 START / CHECK
# ==============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_chat.id, "system_ok"))


async def check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await get_role(update)
    text = f"{t(update.effective_chat.id,'system_ok')}\nRole: {role}"

    if role == "owner":
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT expire_date FROM owners WHERE user_id=%s",
                       (update.effective_user.id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if row:
            remaining = row[0] - datetime.utcnow()
            days = remaining.days
            text += f"\nExpire in: {days} days"

    await update.message.reply_text(text)


# ==============================
# 📖 HELP
# ==============================

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(t(update.effective_chat.id, "help"))


# ==============================
# 💰 RECORD SYSTEM
# ==============================

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await get_role(update)
    if role not in ["master", "owner", "assistant", "operator"]:
        return

    match = re.match(r'^([+-])\s*([\d,]+(?:\.\d{1,2})?)\s*(.*)?$', update.message.text.strip())
    if not match:
        return

    sign = match.group(1)
    amount = Decimal(match.group(2).replace(",", "")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)

    if sign == "-":
        amount = -amount

    note = match.group(3)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO history (chat_id, amount, note, user_name)
        VALUES (%s,%s,%s,%s)
    """, (update.effective_chat.id, amount, note,
          update.message.from_user.first_name))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("OK")


async def showall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, note, user_name FROM history
        WHERE chat_id=%s
        ORDER BY timestamp DESC
    """, (update.effective_chat.id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        await update.message.reply_text(t(update.effective_chat.id, "no_record"))
        return

    text = "Records:\n"
    total = Decimal("0.00")

    for r in rows:
        total += r[0]
        text += f"{r[0]} ({r[1]}) - {r[2]}\n"

    text += f"\nTotal: {total}"
    await update.message.reply_text(text)


# ==============================
# ↩ UNDO
# ==============================

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await get_role(update)
    if role not in ["master", "owner"]:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM history
        WHERE id = (
            SELECT id FROM history
            WHERE chat_id=%s
            ORDER BY timestamp DESC
            LIMIT 1
        )
    """, (update.effective_chat.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("Undone")


# ==============================
# 🗑 RESET
# ==============================

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await get_role(update)
    if role not in ["master", "owner"]:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history WHERE chat_id=%s",
                   (update.effective_chat.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("Cleared")


# ==============================
# 👥 MEMBER MANAGEMENT
# ==============================

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await get_role(update)
    if role not in ["master", "owner"]:
        return

    if not update.message.reply_to_message:
        return

    try:
        new_role = context.args[0]
        if new_role not in ["assistant", "operator"]:
            return
    except:
        return

    target = update.message.reply_to_message.from_user

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO members (user_id, chat_id, role)
        VALUES (%s,%s,%s)
        ON CONFLICT (user_id, chat_id)
        DO UPDATE SET role=%s
    """, (target.id, update.effective_chat.id, new_role, new_role))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("Added")


async def addlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_id, role FROM members
        WHERE chat_id=%s
    """, (update.effective_chat.id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        await update.message.reply_text("Empty")
        return

    text = ""
    for r in rows:
        text += f"{r[0]} - {r[1]}\n"

    await update.message.reply_text(text)


async def resetadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await get_role(update)
    if role not in ["master", "owner"]:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM members WHERE chat_id=%s",
                   (update.effective_chat.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("Members cleared")


# ==============================
# 🔄 RENEW
# ==============================

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(MASTER_ID):
        return

    try:
        target = int(context.args[0])
        days = int(context.args[1])
    except:
        return

    expire = datetime.utcnow() + timedelta(days=days)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO owners (user_id, expire_date)
        VALUES (%s,%s)
        ON CONFLICT (user_id)
        DO UPDATE SET expire_date=%s
    """, (target, expire, expire))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("Renewed")


# ==============================
# 🌍 LANG
# ==============================

async def set_lang(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        lang = context.args[0]
        if lang not in ["zh", "th", "en"]:
            return
    except:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO chat_settings (chat_id, language)
        VALUES (%s,%s)
        ON CONFLICT (chat_id)
        DO UPDATE SET language=%s
    """, (update.effective_chat.id, lang, lang))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("Language updated")


# ==============================
# 🚀 RUN
# ==============================

if __name__ == "__main__":
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("check", check))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("showall", showall))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("addlist", addlist))
    app.add_handler(CommandHandler("resetadd", resetadd))
    app.add_handler(CommandHandler("renew", renew))
    app.add_handler(CommandHandler("lang", set_lang))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.run_polling()
