import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT NOT NULL,
        message_id BIGINT NOT NULL,
        reply_message_id BIGINT,
        user_name TEXT NOT NULL,
        amount NUMERIC NOT NULL,
        created_at TIMESTAMP DEFAULT NOW()
    );
    """)

    conn.commit()
    cur.close()
    conn.close()
