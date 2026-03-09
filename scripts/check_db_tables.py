import sqlite3
import os

db_path = os.path.join("data", "bsc_bot.db")
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", tables)
    conn.close()
except Exception as e:
    print(f"Error: {e}")
