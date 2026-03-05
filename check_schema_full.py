import sqlite3
import os

db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

tables = ["simulation_positions", "pairs", "simulation_trades"]

for table in tables:
    print(f"\n--- Table: {table} ---")
    try:
        cursor.execute(f"PRAGMA table_info({table})")
        columns = cursor.fetchall()
        if not columns:
            print("Table not found.")
        else:
            for col in columns:
                print(col)
    except Exception as e:
        print(f"Error: {e}")

conn.close()
