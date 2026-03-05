import sqlite3
import pandas as pd

db_path = "bsc_bot/data/bsc_bot.db"
conn = sqlite3.connect(db_path)

try:
    print("Checking simulation_trades...")
    # Schema: id, token_address, token_symbol, action, amount, price, tx_hash, status, timestamp, pnl_percentage, pnl_bnb
    query = "SELECT token_symbol, action, amount, price, status, timestamp, pnl_percentage, pnl_bnb FROM simulation_trades ORDER BY timestamp DESC"
    df = pd.read_sql_query(query, conn)
    print(df)
except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
