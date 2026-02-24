import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

if not DATABASE_URL:
    raise ValueError("DATABASE_URL not set")


def get_db_connection():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        # ==============================
        # 群组设置
        # ==============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id BIGINT PRIMARY KEY,
            timezone INTEGER DEFAULT 0,
            work_start TIME DEFAULT '00:00'
        );
        """)

        cursor.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS timezone INTEGER DEFAULT 0;
        """)

        cursor.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS work_start TIME DEFAULT '00:00';
        """)

        # ==============================
        # 账单记录
        # ==============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            message_id BIGINT,
            amount NUMERIC(15,2) NOT NULL,
            user_name TEXT,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """)

        # 🔥 兼容旧库（补 message_id）
        cursor.execute("""
        ALTER TABLE history
        ADD COLUMN IF NOT EXISTS message_id BIGINT;
        """)

        # 🔥 如果旧数据库是 INTEGER → 自动升级为 NUMERIC
        cursor.execute("""
        DO $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM information_schema.columns
                WHERE table_name='history'
                AND column_name='amount'
                AND data_type='integer'
            ) THEN
                ALTER TABLE history
                ALTER COLUMN amount TYPE NUMERIC(15,2)
                USING amount::NUMERIC(15,2);
            END IF;
        END$$;
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_chat_time
        ON history(chat_id, timestamp);
        """)

        # ==============================
        # 操作者
        # ==============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            member_id BIGINT,
            chat_id BIGINT,
            username TEXT,
            PRIMARY KEY (member_id, chat_id)
        );
        """)

        # ==============================
        # Owner
        # ==============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY,
            expire_date TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_admin_expire
        ON admins(expire_date);
        """)

        conn.commit()

    finally:
        conn.close()
