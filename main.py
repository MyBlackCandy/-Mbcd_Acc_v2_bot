import os
import re
import logging
from decimal import Decimal
from datetime import datetime
from telegram import (
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton
)
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes
)
from database import get_db_connection, init_db

TOKEN = os.getenv("TOKEN")
MASTER_ID = os.getenv("MASTER_ID")

if not TOKEN:
    raise ValueError("TOKEN not set")
if not MASTER_ID:
    raise ValueError("MASTER_ID not set")

logging.basicConfig(level=logging.INFO)

# ==============================
# 权限系统
# ==============================

async def is_master(update: Update):
    return str(update.effective_user.id) == str(MASTER_ID)

async def is_owner(update: Update):
    if await is_master(update):
        return True

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT expire_date FROM admins WHERE user_id=%s",
                   (update.effective_user.id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return row and row[0] > datetime.utcnow()

async def is_operator(update: Update):
    if await is_owner(update):
        return True

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT 1 FROM team_members
        WHERE member_id=%s AND chat_id=%s
    """, (update.effective_user.id, update.effective_chat.id))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    return bool(row)

# ==============================
# 开始
# ==============================

async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 机器人已启动\n"
        "━━━━━━━━━━━━━━━\n"
        "发送: +10 或 -5\n"
        "可用 reply 指定对象\n\n"
        "/report 查看最近\n"
        "/all 查看全部\n"
        "/sum 按人汇总\n"
        "/days 按日期查看\n"
        "/undo 撤销\n"
        "/reset 清空"
    )

# ==============================
# 显示账单（可点击）
# ==============================

async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, show_all=False):
    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, amount, user_name, timestamp
        FROM history
        WHERE chat_id=%s
        ORDER BY timestamp ASC
    """, (chat_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        await update.message.reply_text("📋 没有任何记录")
        return

    display = rows if show_all else rows[-6:]
    total = sum(Decimal(r[1]) for r in rows)

    keyboard = []
    text = "📋 记录 (点击查看详情):\n━━━━━━━━━━━━━━━\n"

    for i, r in enumerate(display, 1):
        dt = r[3].strftime("%Y-%m-%d %H:%M")
        label = f"{dt} | {r[1]} ({r[2]})"
        keyboard.append([
            InlineKeyboardButton(label, callback_data=f"item:{r[0]}")
        ])

    text += "━━━━━━━━━━━━━━━\n"
    text += f"合计: {total}"

    await update.message.reply_text(
        text,
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

# ==============================
# 点击某一条记录
# ==============================

async def show_item_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    item_id = query.data.split(":")[1]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, user_name, timestamp
        FROM history
        WHERE id=%s
    """, (item_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    if not row:
        await query.message.edit_text("⚠️ 找不到该记录")
        return

    text = (
        "📄 记录详情\n"
        "━━━━━━━━━━━━━━━\n"
        f"时间: {row[2].strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"对象: {row[1]}\n"
        f"金额: {row[0]}\n"
        "━━━━━━━━━━━━━━━"
    )

    await query.message.edit_text(text)

# ==============================
# ⭐ 按人汇总
# ==============================

async def send_sum_by_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_name, SUM(amount)
        FROM history
        WHERE chat_id=%s
        GROUP BY user_name
        ORDER BY SUM(amount) DESC
    """, (chat_id,))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        await update.message.reply_text("📭 没有任何记录")
        return

    text = "👥 按人汇总:\n━━━━━━━━━━━━━━━\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. {r[0]} : {r[1]}\n"

    await update.message.reply_text(text)

# ==============================
# 记账
# ==============================

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_operator(update):
        return

    text = update.message.text.strip()
    match = re.match(r'^([+-])\s*([\d,]+(?:\.\d{1,2})?)$', text)
    if not match:
        return

    sign = match.group(1)
    number_str = match.group(2).replace(",", "")
    amount = Decimal(number_str)

    if sign == "-":
        amount = -amount

    if update.message.reply_to_message:
        target_user = update.message.reply_to_message.from_user.first_name
    else:
        target_user = update.message.from_user.first_name

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO history (chat_id, amount, user_name) VALUES (%s,%s,%s)",
        (update.effective_chat.id, amount, target_user)
    )
    conn.commit()
    cursor.close()
    conn.close()

    await send_summary(update, context)

# ==============================
# 撤销
# ==============================

async def undo_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_operator(update):
        return

    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, amount FROM history
        WHERE chat_id=%s
        ORDER BY timestamp DESC LIMIT 1
    """, (chat_id,))
    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("⚠️ 没有可撤销的记录")
        cursor.close(); conn.close()
        return

    cursor.execute("DELETE FROM history WHERE id=%s", (row[0],))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"↩️ 已撤销: {row[1]}")
    await send_summary(update, context)

# ==============================
# 重置
# ==============================

async def reset_current(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_operator(update):
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history WHERE chat_id=%s",
                   (update.effective_chat.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("🗑️ 已清空所有记录")

# ==============================
# 启动
# ==============================

if __name__ == "__main__":
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(CommandHandler("report", send_summary))
    app.add_handler(CommandHandler("all", lambda u, c: send_summary(u, c, show_all=True)))
    app.add_handler(CommandHandler("sum", send_sum_by_user))

    app.add_handler(CommandHandler("undo", undo_last))
    app.add_handler(CommandHandler("reset", reset_current))

    app.add_handler(CallbackQueryHandler(show_item_detail, pattern=r"^item:"))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.run_polling()
