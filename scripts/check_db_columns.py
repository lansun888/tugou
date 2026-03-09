import sqlite3
import os

db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("PRAGMA table_info(simulation_positions)")
    columns = cursor.fetchall()
    print("Columns:", [col[1] for col in columns])
    
    # Query what we can
    cursor.execute("SELECT token_name, pair_address FROM simulation_positions WHERE status='active'")
    rows = cursor.fetchall()
    print("Active Positions (name, pair):", rows)
    conn.close()
except Exception as e:
    print(f"Error: {e}")
