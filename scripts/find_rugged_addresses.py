import sqlite3
import os

db_path = r"d:\workSpace\tugou\bsc_bot\data\bsc_bot.db"
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

print("Searching for recent sell trades with large losses...")
sql = "SELECT token_address, token_name, pnl_percentage, note FROM simulation_trades WHERE action='sell' AND pnl_percentage < -50 ORDER BY created_at DESC LIMIT 10"
cursor.execute(sql)
rows = cursor.fetchall()
for r in rows:
    print(r)

print("\nSearching for tokens by name keywords (LongXia, WuJin, TuBoShu)...")
keywords = ['龙虾', '无尽', '土拨', 'Lobster', 'Endless', 'Marmot']
for kw in keywords:
    sql = f"SELECT token_address, token_name FROM simulation_trades WHERE token_name LIKE '%{kw}%' LIMIT 1"
    cursor.execute(sql)
    rows = cursor.fetchall()
    if rows:
        print(f"Found {kw}: {rows}")

conn.close()
