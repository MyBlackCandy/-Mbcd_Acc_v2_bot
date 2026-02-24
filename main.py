import os
import re
from decimal import Decimal
from telegram import Update
from telegram.ext import (
    Application, CommandHandler,
    MessageHandler, ContextTypes, filters
)
from database import get_db_connection, init_db

TOKEN = os.getenv("TOKEN")

amount_pattern = re.compile(r'^([+-])\s*(\d+(?:\.\d+)?)$')

# =====================
# START
# =====================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 บอทเริ่มทำงาน\n"
        "ส่ง: +100 หรือ -50\n"
        "ใช้ reply เพื่อระบุคน\n"
        "/report ดูรายการ\n"
        "/sum สรุปตามคน"
    )

# =====================
# HANDLE MESSAGE
# =====================
async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    match = amount_pattern.match(text)
    if not match:
        return

    sign, num = match.groups()
    amount = Decimal(num)
    if sign == "-":
        amount = -amount

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user.full_name
        reply_msg_id = update.message.reply_to_message.message_id
    else:
        target_user = update.message.from_user.full_name
        reply_msg_id = None

    conn = get_db_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO history
        (chat_id, message_id, reply_message_id, user_name, amount)
        VALUES (%s,%s,%s,%s,%s)
    """, (
        update.effective_chat.id,
        update.message.message_id,
        reply_msg_id,
        target_user,
        amount
    ))
    conn.commit()
    cur.close()
    conn.close()

    await update.message.reply_text(f"✅ บันทึก {amount} ให้ {target_user}")

# =====================
# REPORT
# =====================
async def report(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 ไม่มีข้อมูล")
        return

    text = "📒 รายการล่าสุด:\n"
    for i, r in enumerate(reversed(rows), 1):
        t = r[2].strftime("%Y-%m-%d %H:%M")
        link = f"https://t.me/c/{str(update.effective_chat.id)[4:]}/{r[3]}" if r[3] else "-"
        text += f"{i}. {t} | {r[0]} ({r[1]})\n{link}\n"

    await update.message.reply_text(text)

# =====================
# SUM
# =====================
async def sum_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
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
    cur.close()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 ไม่มีข้อมูล")
        return

    text = "👥 สรุปตามคน:\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. {r[0]} : {r[1]}\n"

    await update.message.reply_text(text)

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

    app.run_polling()
