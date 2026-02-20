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

        # =============================
        # chat_settings
        # =============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS chat_settings (
            chat_id BIGINT PRIMARY KEY
        );
        """)

        # เพิ่ม field แบบปลอดภัย
        cursor.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS base_unit TEXT DEFAULT 'USD';
        """)

        cursor.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS timezone INTEGER DEFAULT 0;
        """)

        cursor.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS work_start TIME DEFAULT '00:00';
        """)

        cursor.execute("""
        ALTER TABLE chat_settings
        ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'zh';
        """)

        # =============================
        # history
        # =============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS history (
            id SERIAL PRIMARY KEY,
            chat_id BIGINT NOT NULL,
            amount NUMERIC(15,2) NOT NULL,
            note TEXT,
            user_name TEXT,
            timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW()
        );
        """)

        cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_history_chat
        ON history(chat_id, timestamp);
        """)

        # =============================
        # members
        # =============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS members (
            user_id BIGINT,
            chat_id BIGINT,
            role TEXT,
            PRIMARY KEY (user_id, chat_id)
        );
        """)

        # =============================
        # owners
        # =============================
        cursor.execute("""
        CREATE TABLE IF NOT EXISTS owners (
            user_id BIGINT PRIMARY KEY,
            expire_date TIMESTAMP WITH TIME ZONE NOT NULL
        );
        """)

        conn.commit()

    finally:
        conn.close()
