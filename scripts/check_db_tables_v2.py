import sqlite3
import os

# Try bsc_bot/data/bsc_bot.db
db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
print(f"Checking {db_path}...")
try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print("Tables:", tables)
    
    if ('simulation_positions',) in tables or ('positions',) in tables:
        table_name = 'simulation_positions' if ('simulation_positions',) in tables else 'positions'
        try:
            cursor.execute(f"SELECT token_name, pair_address, platform FROM {table_name} WHERE status='active'")
            rows = cursor.fetchall()
            print("Active Positions:", rows)
        except Exception as e:
            print(f"Query error: {e}")
            
    conn.close()
except Exception as e:
    print(f"Error: {e}")
