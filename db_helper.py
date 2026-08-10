import os
import psycopg2
from psycopg2.extras import RealDictCursor
from contextlib import contextmanager
from dotenv import load_dotenv

load_dotenv()

DB_HOST = os.environ.get('DB_HOST', 'localhost')
DB_PORT = os.environ.get('DB_PORT', '5432')
DB_NAME = os.environ.get('DB_NAME', 'intelswap')
DB_USER = os.environ.get('DB_USER', 'postgres')
DB_PASSWORD = os.environ.get('DB_PASSWORD', '')

@contextmanager
def get_db_connection():
    conn = psycopg2.connect(
        host=DB_HOST,
        port=DB_PORT,
        dbname=DB_NAME,
        user=DB_USER,
        password=DB_PASSWORD,
        cursor_factory=RealDictCursor,
    )
    try:
        yield conn
    finally:
        conn.close()

def execute_query(query, params=None, fetchone=False, fetchall=False):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            if fetchone:
                result = cursor.fetchone()
                return dict(result) if result else None
            if fetchall:
                results = cursor.fetchall()
                return [dict(row) for row in results]
            conn.commit()
            return None
        finally:
            cursor.close()

def execute_insert(query, params=None):
    with get_db_connection() as conn:
        cursor = conn.cursor()
        try:
            cursor.execute(query, params)
            result = cursor.fetchone()
            conn.commit()
            return dict(result) if result else None
        except Exception as e:
            conn.rollback()
            print(f"Error in execute_insert: {e}")
            return None
        finally:
            cursor.close()