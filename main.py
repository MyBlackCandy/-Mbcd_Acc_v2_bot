import os
import re
import logging
from decimal import Decimal
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes
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
# 工作时间段
# ==============================

def ensure_chat_settings(chat_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT 1 FROM chat_settings WHERE chat_id=%s", (chat_id,))
    if not cursor.fetchone():
        cursor.execute("INSERT INTO chat_settings (chat_id) VALUES (%s)", (chat_id,))
        conn.commit()
    cursor.close()
    conn.close()

def get_work_period(chat_id):
    ensure_chat_settings(chat_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT timezone, work_start FROM chat_settings WHERE chat_id=%s",
                   (chat_id,))
    tz, work_start = cursor.fetchone()
    cursor.close()
    conn.close()

    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=tz)

    today_start = datetime.combine(now_local.date(), work_start)
    if now_local < today_start:
        today_start -= timedelta(days=1)

    start_utc = today_start - timedelta(hours=tz)
    end_utc = start_utc + timedelta(days=1)

    return start_utc, end_utc, tz

# ==============================
# 开始
# ==============================

async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🤖 机器人已启动\n"
        "━━━━━━━━━━━━━━━\n"
        "发送: +10 或 -5\n"
        "可 reply 指定对象\n\n"
        "/report 查看最近\n"
        "/all 查看全部\n"
        "/sum 按人汇总\n"
        "/undo 撤销\n"
        "/reset 清空"
    )
    await send_summary(update, context)

# ==============================
# 帮助
# ==============================

async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 指令说明\n"
        "━━━━━━━━━━━━━━━\n"
        "/start - 状态\n"
        "/report - 最近记录\n"
        "/all - 全部记录\n"
        "/sum - 按人汇总\n"
        "/undo - 撤销上一条\n"
        "/reset - 清空全部\n"
        "/add - 回复用户添加操作者\n"
        "/remove - 回复用户删除操作者\n"
        "━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text)

# ==============================
# 账单显示
# ==============================

async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, show_all=False):
    chat_id = update.effective_chat.id
    start_utc, end_utc, tz = get_work_period(chat_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, user_name, timestamp
        FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
        ORDER BY timestamp ASC
    """, (chat_id, start_utc, end_utc))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    if not rows:
        await update.message.reply_text("📋 今天没有记录")
        return

    total = sum(Decimal(r[0]) for r in rows)
    display = rows if show_all else rows[-6:]
    start_index = len(rows) - len(display) + 1

    text = "📋 今天记录:\n━━━━━━━━━━━━━━━\n"
    for i, r in enumerate(display):
        local_time = r[2] + timedelta(hours=tz)
        text += f"{start_index + i}. {local_time.strftime('%H:%M')} | {r[0]} ({r[1]})\n"

    text += "━━━━━━━━━━━━━━━\n"
    text += f"合计: {total}"

    await update.message.reply_text(text)

# ==============================
# ⭐ 按人汇总
# ==============================

async def send_sum_by_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    start_utc, end_utc, _ = get_work_period(chat_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT user_name, SUM(amount)
        FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
        GROUP BY user_name
        ORDER BY SUM(amount) DESC
    """, (chat_id, start_utc, end_utc))
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
    start_utc, end_utc, _ = get_work_period(chat_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT id, amount FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
        ORDER BY timestamp DESC LIMIT 1
    """, (chat_id, start_utc, end_utc))
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

    chat_id = update.effective_chat.id
    start_utc, end_utc, _ = get_work_period(chat_id)

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
    """, (chat_id, start_utc, end_utc))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("🗑️ 今天已清空")

# ==============================
# 启动
# ==============================

if __name__ == "__main__":
    init_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(CommandHandler("help", help_menu))

    app.add_handler(CommandHandler("report", send_summary))
    app.add_handler(CommandHandler("all", lambda u, c: send_summary(u, c, show_all=True)))
    app.add_handler(CommandHandler("sum", send_sum_by_user))

    app.add_handler(CommandHandler("undo", undo_last))
    app.add_handler(CommandHandler("reset", reset_current))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.run_polling()
