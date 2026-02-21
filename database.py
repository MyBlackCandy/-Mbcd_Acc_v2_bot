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

        # ==================================================
        # 群组设置
        # ==================================================
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

        # ==================================================
        # 账单记录
        # ==================================================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            amount NUMERIC(18,2) NOT NULL,
            quantity NUMERIC,
            item TEXT,
            user_name TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # 🔥 升级旧数据库 amount INTEGER → NUMERIC
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
                ALTER COLUMN amount TYPE NUMERIC(18,2)
                USING amount::NUMERIC(18,2);
            END IF;
        END$$;
        """)

        # 🔥 自动补充字段（防止旧版本缺失）
        cursor.execute("""
        ALTER TABLE history
        ADD COLUMN IF NOT EXISTS quantity NUMERIC;
        """)

        cursor.execute("""
        ALTER TABLE history
        ADD COLUMN IF NOT EXISTS item TEXT;
        """)

        # 索引
        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_chat_time
        ON history(chat_id, timestamp);
        """)

        # ==================================================
        # 操作者
        # ==================================================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS team_members (
            member_id BIGINT,
            chat_id BIGINT,
            username TEXT,
            PRIMARY KEY (member_id, chat_id)
        );
        """)

        # ==================================================
        # Owner（无时区版本）
        # ==================================================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY,
            expire_date TIMESTAMP NOT NULL
        );
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_admin_expire
        ON admins(expire_date);
        """)






        # Owner table
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            user_id BIGINT PRIMARY KEY,
            expire_date TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """)

        # Owner ↔ Group mapping
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS owner_groups (
            user_id BIGINT,
            chat_id BIGINT,
            PRIMARY KEY (user_id, chat_id)
        );
        """)
        

        conn.commit()

    finally:
        conn.close()
