import os
import re
import logging
from decimal import Decimal

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
# 开始（完整状态面板）
# ==============================

async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id

    ensure_chat_settings(chat_id)

    conn = get_db_connection()
    cursor = conn.cursor()

    # 读取时区和工作时间
    cursor.execute("""
        SELECT timezone, work_start
        FROM chat_settings
        WHERE chat_id=%s
    """, (chat_id,))
    tz, work_start = cursor.fetchone()

    # 当前时间
    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=tz)

    # 当前工作轮次
    start_utc, end_utc, _ = get_work_period(chat_id)
    start_local = start_utc + timedelta(hours=tz)
    end_local = end_utc + timedelta(hours=tz)

    # 操作者数量
    cursor.execute("""
        SELECT COUNT(*) FROM team_members
        WHERE chat_id=%s
    """, (chat_id,))
    operator_count = cursor.fetchone()[0]

    # 本轮记录数量
    cursor.execute("""
        SELECT COUNT(*) FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
    """, (chat_id, start_utc, end_utc))
    record_count = cursor.fetchone()[0]

    cursor.close()
    conn.close()

    record_status = "有记录 📊" if record_count > 0 else "暂无记录 📭"

    text = (
        "🤖 机器人状态\n"
        "━━━━━━━━━━━━━━━\n"
        f"当前时区: UTC{tz:+}\n"
        f"当前时间: {now_local.strftime('%Y-%m-%d %H:%M:%S')}\n"
        "\n"
        "📅 当前工作轮次:\n"
        f"{start_local.strftime('%Y-%m-%d %H:%M')}  →  "
        f"{end_local.strftime('%Y-%m-%d %H:%M')}\n"
        "\n"
        f"操作者数量: {operator_count} 人\n"
        f"本轮状态: {record_status}\n"
        "━━━━━━━━━━━━━━━\n"
        "系统运行正常 ✅"
    )

    await update.message.reply_text(text)
# ==============================
# 帮助菜单
# ==============================
async def help_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 机器人使用说明\n"
        "━━━━━━━━━━━━━━━\n"
        "🧾 记账输入格式:\n"
        
        "+U金额 币数量 币名\n"
        "\n"
        "示例:\n"
        
        "+95 0.0485761 ETH\n"
        "+500 0.0002 BTC\n"
       
        "\n"
        "━━━━━━━━━━━━━━━\n"
        "📊 常用指令:\n"
        "/start 或 /开始 - 查看系统状态\n"
        "/report 或 /账单 - 查看当前轮次\n"
        "/all 或 /全部 - 查看全部记录\n"
        "/undo 或 /撤销 - 撤销上一条\n"
        "/reset 或 /重置 - 清空当前轮次\n"
        "/check 或 /检查 - 查看身份\n"
        "\n"
        "👥 权限相关:\n"
        "/add 或 /添加 - 添加操作者\n"
        "/remove 或 /删除 - 删除操作者\n"
        "\n"
        "⏰ 时间设置:\n"
        "/timezone 或 /设置时区 +8\n"
        "/worktime 或 /设置时间 14:00\n"
        "\n"
        "👑 Master:\n"
        "/renew 或 /续费 用户ID 天数\n"
        "━━━━━━━━━━━━━━━"
    )
    await update.message.reply_text(text)
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
# 账单显示
# ==============================
async def send_summary(update: Update, context: ContextTypes.DEFAULT_TYPE, show_all=False):
    chat_id = update.effective_chat.id
    start_utc, end_utc, tz = get_work_period(chat_id)

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        SELECT amount, quantity, item, user_name, timestamp
        FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
        ORDER BY timestamp ASC
    """, (chat_id, start_utc, end_utc))

    rows = cursor.fetchall()

    if not rows:
        await update.message.reply_text("📋 今天没有记录")
        cursor.close()
        conn.close()
        return

    summary = {}
    total = sum(Decimal(r[0]) for r in rows)

    display = rows if show_all else rows[-5:]

    text = "📋 本轮记录:\n━━━━━━━━━━━━━━━\n"

    if len(rows) > 5 and not show_all:
        text += "...\n"

    start_number = len(rows) - len(display) + 1

    for index, r in enumerate(display, start=start_number):
        amount, qty, item, user, ts = r

        # ✅ 转换成本地时间
        local_time = ts + timedelta(hours=tz)

        line = f"{index}. {local_time.strftime('%H:%M')} | {Decimal(amount):,.2f}"

        if qty and item:
            line += f" ({qty} {item})"

        text += line + "\n"

    # ===== 分类汇总 =====
    for r in rows:
        amount, qty, item, *_ = r
        key = item if item else "默认"

        if key not in summary:
            summary[key] = {
                "total": Decimal("0.00"),
                "qty": Decimal("0.00"),
                "count": 0
            }

        summary[key]["total"] += Decimal(amount)
        summary[key]["count"] += 1

        if qty:
            summary[key]["qty"] += Decimal(qty)

    text += "━━━━━━━━━━━━━━━\n"
    text += "📊 分类汇总:\n"

    for k, v in summary.items():
        line = f"{k}: {v['total']:,.2f}"
        if v["qty"] > 0:
            line += f" | 数量: {v['qty']}"
        line += f" | {v['count']} 笔"
        text += line + "\n"

    text += "━━━━━━━━━━━━━━━\n"
    text += f"💰 总计: {total:,.2f}"

    cursor.close()
    conn.close()

    await update.message.reply_text(text)

# ==============================
# 记账（必须存在）
# ==============================

async def handle_msg(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_operator(update):
        return

    text = update.message.text.strip()

    # รองรับ:
    # +100
    # +100 USD
    # +95 0.048 ETH
    # +1,200.50 0.002 BTC
    match = re.match(
        r'^([+-])\s*([\d,]+(?:\.\d{1,2})?)'
        r'(?:\s+([\d\.]+))?'
        r'(?:\s+([A-Za-z]+))?$',
        text
    )

    if not match:
        return

    sign = match.group(1)
    amount_str = match.group(2).replace(",", "")
    quantity = match.group(3)
    item = match.group(4)

    amount = Decimal(amount_str)

    if sign == "-":
        amount = -amount

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO history (chat_id, amount, quantity, item, user_name)
        VALUES (%s,%s,%s,%s,%s)
    """, (
        update.effective_chat.id,
        amount,
        Decimal(quantity) if quantity else None,
        item.upper() if item else None,
        update.message.from_user.first_name
    ))

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
        SELECT id, amount, quantity, item
        FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
        ORDER BY timestamp DESC
        LIMIT 1
    """, (chat_id, start_utc, end_utc))

    row = cursor.fetchone()

    if not row:
        await update.message.reply_text("⚠️ 当前没有可撤销的记录")
        cursor.close(); conn.close()
        return

    cursor.execute("DELETE FROM history WHERE id=%s", (row[0],))
    conn.commit()

    deleted_text = f"{Decimal(row[1]):,.2f}"
    if row[2] and row[3]:
        deleted_text += f" ({row[2]} {row[3]})"

    cursor.close()
    conn.close()

    await update.message.reply_text(f"↩️ 已撤销: {deleted_text}")
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
        SELECT COUNT(*) FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
    """, (chat_id, start_utc, end_utc))

    count = cursor.fetchone()[0]

    cursor.execute("""
        DELETE FROM history
        WHERE chat_id=%s
        AND timestamp BETWEEN %s AND %s
    """, (chat_id, start_utc, end_utc))

    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(f"🗑 已清空 {count} 条记录")
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

    await update.message.reply_text(f"✅ 已添加操作者: {target.first_name}")

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

    await update.message.reply_text(f"🗑️ 已删除操作者: {target.first_name}")

# ==============================
# 设置时区
# ==============================
async def set_timezone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update):
        return

    try:
        tz = int(context.args[0])
        if tz < -12 or tz > 14:
            raise ValueError
    except:
        await update.message.reply_text("用法: /设置时区 +8  (范围 -12 ~ +14)")
        return

    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        INSERT INTO chat_settings (chat_id, timezone)
        VALUES (%s,%s)
        ON CONFLICT (chat_id)
        DO UPDATE SET timezone=%s
    """, (chat_id, tz, tz))

    conn.commit()
    cursor.close()
    conn.close()

    # 🔥 计算当前本地时间
    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=tz)

    await update.message.reply_text(
        f"✅ 时区已设置为 UTC{tz:+}\n"
        f"🕒 当前时间: {now_local.strftime('%Y-%m-%d %H:%M:%S')}"
    )

# ==============================
# 设置时间
# ==============================

async def set_worktime(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_owner(update):
        return

    try:
        time_str = context.args[0]
        work_time = datetime.strptime(time_str, "%H:%M").time()
    except:
        await update.message.reply_text("用法: /设置时间 14:00")
        return

    chat_id = update.effective_chat.id

    conn = get_db_connection()
    cursor = conn.cursor()

    # 先获取当前时区
    cursor.execute("SELECT timezone FROM chat_settings WHERE chat_id=%s", (chat_id,))
    row = cursor.fetchone()
    tz = row[0] if row else 0

    # 更新工作时间
    cursor.execute("""
        INSERT INTO chat_settings (chat_id, work_start)
        VALUES (%s,%s)
        ON CONFLICT (chat_id)
        DO UPDATE SET work_start=%s
    """, (chat_id, time_str, time_str))

    conn.commit()
    cursor.close()
    conn.close()

    # 🔥 计算当前本地时间
    now_utc = datetime.utcnow()
    now_local = now_utc + timedelta(hours=tz)

    # 🔥 计算当前轮次开始时间
    today_start_local = datetime.combine(now_local.date(), work_time)

    if now_local < today_start_local:
        today_start_local -= timedelta(days=1)

    today_end_local = today_start_local + timedelta(days=1)

    await update.message.reply_text(
        f"✅ 工作时间设置为 {time_str}\n\n"
        f"🕒 当前时间: {now_local.strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"📅 本轮时间:\n"
        f"{today_start_local.strftime('%Y-%m-%d %H:%M')}  →  "
        f"{today_end_local.strftime('%Y-%m-%d %H:%M')}"
    )
# ==============================
# 权限检查
# ==============================

async def check_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    # Master
    if await is_master(update):
        await update.message.reply_text(
            f"🆔 ID: {user_id}\n"
            "👑 身份: Master\n"
            "权限: 最高权限"
        )
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    # Owner
    cursor.execute(
        "SELECT expire_date FROM admins WHERE user_id=%s",
        (user_id,)
    )
    row = cursor.fetchone()

    if row and row[0] > datetime.utcnow():
        remaining = row[0] - datetime.utcnow()

        total_seconds = int(remaining.total_seconds())

        days = total_seconds // 86400
        hours = (total_seconds % 86400) // 3600
        minutes = (total_seconds % 3600) // 60

        cursor.close()
        conn.close()

        await update.message.reply_text(
            f"🆔 ID: {user_id}\n"
            "👑 身份: Owner\n"
            f"剩余时间: {days} 天 {hours} 小时 {minutes} 分钟"
        )
        return

    # Operator
    cursor.execute("""
        SELECT 1 FROM team_members
        WHERE member_id=%s AND chat_id=%s
    """, (user_id, update.effective_chat.id))

    if cursor.fetchone():
        cursor.close()
        conn.close()

        await update.message.reply_text(
            f"🆔 ID: {user_id}\n"
            "👥 身份: 操作者"
        )
        return

    cursor.close()
    conn.close()

    # 普通成员
    await update.message.reply_text(
        f"🆔 ID: {user_id}\n"
        "❌ 身份: 普通成员\n"
        "无操作权限"
    )

# ==============================
# Master 续费
# ==============================
from datetime import datetime, timedelta, timezone

async def renew_owner(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not await is_master(update):
        return

    try:
        if update.message.reply_to_message:
            target_id = update.message.reply_to_message.from_user.id
            days = int(context.args[0])
        else:
            target_id = int(context.args[0])
            days = int(context.args[1])
    except:
        await update.message.reply_text("用法: /续费 用户ID 天数")
        return

    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("SELECT expire_date FROM admins WHERE user_id=%s", (target_id,))
    row = cursor.fetchone()

    now = datetime.now(timezone.utc)  # 🔥 สำคัญ

    if row and row[0] > now:
        new_expire = row[0] + timedelta(days=days)
    else:
        new_expire = now + timedelta(days=days)

    cursor.execute("""
        INSERT INTO admins (user_id, expire_date)
        VALUES (%s,%s)
        ON CONFLICT (user_id)
        DO UPDATE SET expire_date=%s
    """, (target_id, new_expire, new_expire))

    conn.commit()
    cursor.close()
    conn.close()

    await update.message.reply_text(
        f"✅ 已续费 {days} 天\n"
        f"到期时间: {new_expire.strftime('%Y-%m-%d %H:%M:%S')}"
    )
# ==============================
# 启动
# ==============================

if __name__ == "__main__":
    init_db()

    app = Application.builder().token(TOKEN).build()

    # 中文命令处理
    # ==============================


    # 状态
    app.add_handler(CommandHandler("start", start_bot))
    app.add_handler(MessageHandler(filters.Regex(r"^/开始$"), start_bot))

    # 帮助
    app.add_handler(CommandHandler("help", help_menu))
    app.add_handler(MessageHandler(filters.Regex(r"^/帮助$"), help_menu))
    
    # 检查
    app.add_handler(CommandHandler("check", check_status))
    app.add_handler(MessageHandler(filters.Regex(r"^/检查$"), check_status))

    # 账单
    app.add_handler(CommandHandler("report", send_summary))
    app.add_handler(MessageHandler(filters.Regex(r"^/账单$"), send_summary))
    

    # 全部
    app.add_handler(CommandHandler("all", lambda u, c: send_summary(u, c, show_all=True)))
    app.add_handler(MessageHandler(filters.Regex(r"^/全部$"), lambda u, c: send_summary(u, c, show_all=True)))

    # 撤销
    app.add_handler(CommandHandler("undo", undo_last))
    app.add_handler(MessageHandler(filters.Regex(r"^/撤销$"), undo_last))

    # 重置
    app.add_handler(CommandHandler("reset", reset_current))
    app.add_handler(MessageHandler(filters.Regex(r"^/重置$"), reset_current))

    # 添加操作者
    app.add_handler(CommandHandler("add", add_member))
    app.add_handler(MessageHandler(filters.Regex(r"^/添加$"), add_member))

    # 删除操作者
    app.add_handler(CommandHandler("remove", remove_member))
    app.add_handler(MessageHandler(filters.Regex(r"^/删除$"), remove_member))

    # 设置时区
    app.add_handler(CommandHandler("timezone", set_timezone))
    app.add_handler(MessageHandler(filters.Regex(r"^/设置时区"), set_timezone))

    # 设置工作时间
    app.add_handler(CommandHandler("worktime", set_worktime))
    app.add_handler(MessageHandler(filters.Regex(r"^/设置时间"), set_worktime))

    # 续费
    app.add_handler(CommandHandler("renew", renew_owner))
    app.add_handler(MessageHandler(filters.Regex(r"^/续费"), renew_owner))



    # 普通文本记账
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_msg))

    app.run_polling()
