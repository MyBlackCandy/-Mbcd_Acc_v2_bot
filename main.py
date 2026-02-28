import os
import re
import logging
import tempfile
from decimal import Decimal
from datetime import datetime
from telegram import Update
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, ContextTypes, filters
)
from database import get_db_connection, init_db

# logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise ValueError("TOKEN environment variable not set")

# pattern: +100 or -50 or +100.5
amount_pattern = re.compile(r'^([+-])\s*(\d+(?:\.\d+)?)$')

def safe_get_name(user):
    """Return best-available display name for a telegram.User-like object"""
    if user is None:
        return "Unknown"
    # try common attributes safely
    name = None
    # some PTB versions have full_name
    if hasattr(user, "full_name") and user.full_name:
        name = user.full_name
    else:
        parts = []
        if getattr(user, "first_name", None):
            parts.append(user.first_name)
        if getattr(user, "last_name", None):
            parts.append(user.last_name)
        if parts:
            name = " ".join(parts)
    if not name:
        # fallback to username or id
        if getattr(user, "username", None):
            name = f"@{user.username}"
        elif getattr(user, "id", None):
            name = str(user.id)
        else:
            name = "Unknown"
    return name

# =====================
# START
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        await update.message.reply_text(
            "🤖 บอทเริ่มทำงาน\n"
            "ส่ง: +100 หรือ -50\n"
            "ใช้ reply เพื่อระบุคน\n"
            "/report ดูรายการ\n"
            "/sum สรุปตามคน"
        )
    except Exception as e:
        logger.exception("start handler failed: %s", e)

# =====================
# HANDLE MESSAGE
# =====================
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        if not update.message or not update.message.text:
            return

        text = update.message.text.strip()
        match = amount_pattern.match(text)
        if not match:
            return

        sign, num = match.groups()
        # use Decimal for money
        amount = Decimal(num)
        if sign == "-":
            amount = -amount

        # determine target user
        if update.message.reply_to_message:
            target_user_obj = update.message.reply_to_message.from_user
            reply_msg_id = update.message.reply_to_message.message_id
        else:
            target_user_obj = update.message.from_user
            reply_msg_id = None

        target_user = safe_get_name(target_user_obj)

        # db insert (with try/except so a DB error doesn't kill the bot)
        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO history
                (chat_id, message_id, reply_message_id, user_name, amount, created_at)
                VALUES (%s,%s,%s,%s,%s, now())
            """, (
                update.effective_chat.id,
                update.message.message_id,
                reply_msg_id,
                target_user,
                amount
            ))
            conn.commit()
            cur.close()
        except Exception:
            logger.exception("DB insert failed in handle_msg")
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            await update.message.reply_text("❗ เกิดข้อผิดพลาดการบันทึกข้อมูล กรุณาลองใหม่")
            return
        finally:
            if conn:
                try:
                    conn.close()
                except Exception:
                    pass

        await update.message.reply_text(f"✅ บันทึก {amount} ให้ {target_user}")
    except Exception as e:
        logger.exception("handle_msg unexpected error: %s", e)

# =====================
# REPORT
# =====================
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT amount, user_name, created_at, reply_message_id
            FROM history
            WHERE chat_id=%s
            ORDER BY id DESC
            LIMIT 10
        """, (update.effective_chat.id,))
        rows = cur.fetchall()
    except Exception:
        logger.exception("DB error in report")
        await update.message.reply_text("❗ เกิดข้อผิดพลาดขณะเรียกข้อมูล")
        return
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

    if not rows:
        await update.message.reply_text("📭 ไม่มีข้อมูล")
        return

    text_lines = ["📒 รายการล่าสุด:"]
    # rows are ordered desc; we want oldest->newest when showing list
    for i, r in enumerate(reversed(rows), 1):
        amount, user_name, created_at, reply_msg_id = r
        # created_at could be tz-aware or naive; format safely
        try:
            t = created_at.strftime("%Y-%m-%d %H:%M")
        except Exception:
            t = str(created_at)
        # create t.me link only for supergroups (id starts with -100)
        chat_id_str = str(update.effective_chat.id)
        if reply_msg_id and chat_id_str.startswith("-100"):
            short_chat_id = chat_id_str[4:]
            link = f"https://t.me/c/{short_chat_id}/{reply_msg_id}"
        else:
            link = "-" if not reply_msg_id else str(reply_msg_id)
        text_lines.append(f"{i}. {t} | {amount} ({user_name})\n{link}")

    await update.message.reply_text("\n".join(text_lines))

# =====================
# SUM
# =====================
async def sum_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("""
            SELECT user_name, SUM(amount)
            FROM history
            WHERE chat_id=%s
            GROUP BY user_name
            ORDER BY SUM(amount) DESC
        """, (update.effective_chat.id,))
        rows = cur.fetchall()
    except Exception:
        logger.exception("DB error in sum_user")
        await update.message.reply_text("❗ เกิดข้อผิดพลาดขณะสรุปข้อมูล")
        return
    finally:
        try:
            cur.close()
            conn.close()
        except Exception:
            pass

    if not rows:
        await update.message.reply_text("📭 ไม่มีข้อมูล")
        return

    text = ["👥 สรุปตามคน:"]
    for i, (name, total) in enumerate(rows, 1):
        text.append(f"{i}. {name} : {total}")
    await update.message.reply_text("\n".join(text))

# =====================
# MAIN
# =====================
if __name__ == "__main__":
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("report", report))
    app.add_handler(CommandHandler("sum", sum_user))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    logger.info("Bot started polling...")
    app.run_polling()
