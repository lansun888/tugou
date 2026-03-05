import sqlite3
import os
import json
from datetime import datetime

db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
conn = sqlite3.connect(db_path)
conn.row_factory = sqlite3.Row
cursor = conn.cursor()

# Check sample data
print("--- Sample Position ---")
cursor.execute("SELECT * FROM simulation_positions LIMIT 1")
row = cursor.fetchone()
if row:
    print(dict(row))

print("\n--- Sample Trade (Sell) ---")
cursor.execute("SELECT * FROM simulation_trades WHERE action='sell' LIMIT 1")
row = cursor.fetchone()
if row:
    print(dict(row))

conn.close()
