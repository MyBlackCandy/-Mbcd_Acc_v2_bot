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

    # 私聊直接允许 master
    if update.effective_chat.type == "private":
        return await is_master(update)

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
        "/days - 按日期查看\n"
        "/undo - 撤销上一条\n"
        "/reset - 清空全部\n"
        "/add - 回复用户添加操作者\n"
        "/remove - 回复用户删除操作者\n"
        "━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text)

# ==============================
# 显示账单
# ==============================

async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, show_all=False):
    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, user_name, timestamp
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

    total = sum(Decimal(r[0]) for r in rows)
    display = rows if show_all else rows[-6:]
    start_index = len(rows) - len(display) + 1

    text = "📋 记录:\n━━━━━━━━━━━━━━━\n"
    for i, r in enumerate(display):
        dt = r[2].strftime("%Y-%m-%d %H:%M")
        text += f"{start_index + i}. {dt} | {r[0]} ({r[1]})\n"

    text += "━━━━━━━━━━━━━━━\n"
    text += f"合计: {total}"

    await update.message.reply_text(text)

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

    # 去掉 @botname
    text = re.sub(r'@\w+', '', text).strip()

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
# 按日期查看
# ==============================

async def show_days(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT DISTINCT DATE(timestamp)
        FROM history
        WHERE chat_id=%s
        ORDER BY DATE(timestamp) DESC
    """, (chat_id,))
    days = cursor.fetchall()
    cursor.close()
    conn.close()

    if not days:
        await update.message.reply_text("📭 没有任何记录")
        return

    keyboard = []
    for d in days:
        day_str = d[0].strftime("%Y-%m-%d")
        keyboard.append([
            InlineKeyboardButton(day_str, callback_data=f"day:{day_str}")
        ])

    await update.message.reply_text(
        "📅 选择要查看的日期:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )

async def show_day_detail(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    day = query.data.split(":")[1]

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT amount, user_name, timestamp
        FROM history
        WHERE chat_id=%s AND DATE(timestamp)=%s
        ORDER BY timestamp ASC
    """, (query.message.chat_id, day))
    rows = cursor.fetchall()
    cursor.close()
    conn.close()

    total = sum(Decimal(r[0]) for r in rows)

    text = f"📅 {day} 记录:\n━━━━━━━━━━━━━━━\n"
    for i, r in enumerate(rows, 1):
        text += f"{i}. {r[2].strftime('%H:%M')} | {r[0]} ({r[1]})\n"

    text += "━━━━━━━━━━━━━━━\n"
    text += f"合计: {total}"

    await query.message.edit_text(text)

# ==============================
# 添加操作者
# ==============================

async def add_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ 请用回复方式添加成员")
        return

    target = update.message.reply_to_message.from_user

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO team_members (member_id, chat_id, username)
        VALUES (%s,%s,%s)
        ON CONFLICT (member_id, chat_id)
        DO UPDATE SET username=%s
    """, (target.id, update.effective_chat.id,
          target.first_name, target.first_name))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"✅ 已添加: {target.first_name}")

# ==============================
# 删除操作者
# ==============================

async def remove_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update):
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("⚠️ 请用回复方式删除成员")
        return

    target = update.message.reply_to_message.from_user

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        DELETE FROM team_members
        WHERE member_id=%s AND chat_id=%s
    """, (target.id, update.effective_chat.id))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"🗑️ 已删除: {target.first_name}")

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

    app.add_handler(CommandHandler("days", show_days))
    app.add_handler(CallbackQueryHandler(show_day_detail, pattern=r"^day:"))

    app.add_handler(CommandHandler("undo", undo_last))
    app.add_handler(CommandHandler("reset", reset_current))

    app.add_handler(CommandHandler("add", add_member))
    app.add_handler(CommandHandler("remove", remove_member))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.run_polling()
