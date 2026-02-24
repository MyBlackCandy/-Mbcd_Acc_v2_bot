import os
import psycopg2
from psycopg2.extras import RealDictCursor

DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    if not DATABASE_URL:
        raise Exception("DATABASE_URL not set")
    return psycopg2.connect(DATABASE_URL, sslmode="require")

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id SERIAL PRIMARY KEY,
        chat_id BIGINT,
        amount NUMERIC(20,2),
        user_name TEXT,
        timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS team_members (
        member_id BIGINT,
        chat_id BIGINT,
        username TEXT,
        PRIMARY KEY (member_id, chat_id)
    );
    """)

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id BIGINT PRIMARY KEY,
        expire_date TIMESTAMP
    );
    """)

    conn.commit()
    cursor.close()
    conn.close()
