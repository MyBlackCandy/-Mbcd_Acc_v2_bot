import os
import re
from decimal import Decimal, ROUND_HALF_UP
from datetime import datetime, timedelta
from collections import defaultdict

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters, ContextTypes

from database import get_db_connection, init_db

TOKEN = os.getenv("TOKEN")
MASTER_ID = os.getenv("MASTER_ID")

if not TOKEN or not MASTER_ID:
    raise ValueError("TOKEN or MASTER_ID not set")


# =======================
# ROLE SYSTEM
# =======================

async def get_role(update: Update):
    if str(update.effective_user.id) == str(MASTER_ID):
        return "master"

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT expire_date FROM owners WHERE user_id=%s",
                   (update.effective_user.id,))
    row = cursor.fetchone()
    if row and row[0] > datetime.utcnow():
        cursor.close(); conn.close()
        return "owner"

    cursor.execute("""
        SELECT role FROM members
        WHERE user_id=%s AND chat_id=%s
    """, (update.effective_user.id, update.effective_chat.id))
    row = cursor.fetchone()

    cursor.close()
    conn.close()

    return row[0] if row else "normal"


def role_required(roles):
    async def wrapper(update: Update):
        return await get_role(update) in roles
    return wrapper


# =======================
# START
# =======================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("系统运行正常 ✅")


# =======================
# RENEW (Master only)
# =======================

async def renew(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) != "master":
        return

    try:
        target = int(context.args[0])
        days = int(context.args[1])
    except:
        await update.message.reply_text("用法: /renew 用户ID 天数")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    expire = datetime.utcnow() + timedelta(days=days)

    cursor.execute("""
        INSERT INTO owners (user_id, expire_date)
        VALUES (%s,%s)
        ON CONFLICT (user_id)
        DO UPDATE SET expire_date=%s
    """, (target, expire, expire))

    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"已续费 {days} 天")


# =======================
# ADD ROLE
# =======================

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    role = await get_role(update)
    if role not in ["master", "owner"]:
        return

    if not update.message.reply_to_message:
        await update.message.reply_text("请回复用户添加")
        return

    try:
        new_role = context.args[0]
    except:
        await update.message.reply_text("用法: 回复用户 /add assistant/operator")
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

    await update.message.reply_text(f"已添加 {new_role}")


# =======================
# ADD LIST
# =======================

async def addlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) not in ["master", "owner"]:
        return

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
        await update.message.reply_text("暂无成员")
        return

    text = "成员列表:\n"
    for uid, role in rows:
        text += f"{uid} - {role}\n"

    await update.message.reply_text(text)


# =======================
# RESET ADD
# =======================

async def resetadd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) not in ["master", "owner"]:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM members WHERE chat_id=%s",
                   (update.effective_chat.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("成员已清空")


# =======================
# TIMEZONE
# =======================

async def timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) not in ["master", "owner"]:
        return

    try:
        tz = int(context.args[0])
    except:
        await update.message.reply_text("用法: /timezone 8")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE chat_settings
        SET timezone=%s
        WHERE chat_id=%s
    """, (tz, update.effective_chat.id))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"时区设置为 UTC{tz:+}")


# =======================
# SET TIME
# =======================

async def settime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) not in ["master", "owner"]:
        return

    try:
        time_str = context.args[0]
    except:
        await update.message.reply_text("用法: /settime 14:00")
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        UPDATE chat_settings
        SET work_start=%s
        WHERE chat_id=%s
    """, (time_str, update.effective_chat.id))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"工作时间设置为 {time_str}")


# =======================
# UNDO
# =======================

async def undo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) not in ["master", "owner"]:
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

    await update.message.reply_text("已撤销最后一笔")


# =======================
# RESET
# =======================

async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) not in ["master", "owner"]:
        return

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM history WHERE chat_id=%s",
                   (update.effective_chat.id,))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("本轮记录已清空")


# =======================
# SHOW ALL
# =======================

async def showall(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await send_summary(update, context)


# =======================
# 记账
# =======================

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if await get_role(update) not in ["master", "owner", "assistant", "operator"]:
        return

    match = re.match(r'^([+-])\s*([\d,]+(?:\.\d{1,2})?)\s*(.*)?$',
                     update.message.text.strip())
    if not match:
        return

    sign = match.group(1)
    number = Decimal(match.group(2).replace(",", "")).quantize(
        Decimal("0.01"), rounding=ROUND_HALF_UP)
    note = match.group(3)

    if sign == "-":
        number = -number

    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute("""
        INSERT INTO history (chat_id, amount, note, user_name)
        VALUES (%s,%s,%s,%s)
    """, (update.effective_chat.id, number, note,
          update.message.from_user.first_name))
    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text("已记录")


# =======================
# 启动
# =======================

if __name__ == "__main__":
    init_db()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("renew", renew))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("addlist", addlist))
    app.add_handler(CommandHandler("resetadd", resetadd))
    app.add_handler(CommandHandler("timezone", timezone))
    app.add_handler(CommandHandler("settime", settime))
    app.add_handler(CommandHandler("undo", undo))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("showall", showall))

    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.run_polling()
