import os
import psycopg2

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_db_connection()
    cur = conn.cursor()

    # history table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT,
        message_id BIGINT,
        amount NUMERIC,
        user_name TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)

    # admins table
    cur.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id BIGINT PRIMARY KEY,
        expire_date TIMESTAMP
    )
    """)

    # team members
    cur.execute("""
    CREATE TABLE IF NOT EXISTS team_members (
        member_id BIGINT,
        chat_id BIGINT,
        username TEXT,
        PRIMARY KEY (member_id, chat_id)
    )
    """)

    conn.commit()
    cur.close()
    conn.close()
