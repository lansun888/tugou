import sqlite3
import os

db_path = "bsc_bot/data/bsc_bot.db"
if not os.path.exists(db_path):
    print(f"Error: {db_path} not found")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

try:
    print('LIVE POSITIONS:')
    # Check if table exists first
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='positions'")
    if cursor.fetchone():
        rows = cursor.execute("SELECT token_address, token_name, buy_price_bnb, current_price, pnl_percentage FROM positions WHERE status='active'").fetchall()
        for row in rows:
            print(row)
    else:
        print("Table 'positions' does not exist")

    print('SIMULATION POSITIONS:')
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='simulation_positions'")
    if cursor.fetchone():
        rows = cursor.execute("SELECT token_address, token_name, buy_price_bnb, current_price, pnl_percentage FROM simulation_positions WHERE status='active'").fetchall()
        for row in rows:
            print(row)
    else:
        print("Table 'simulation_positions' does not exist")
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
