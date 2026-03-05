import sqlite3
import pandas as pd

db_path = "bsc_bot/data/bsc_bot.db"
conn = sqlite3.connect(db_path)

try:
    print("Checking simulation_positions (active):")
    query = "SELECT token_name, status, sold_portions FROM simulation_positions"
    df = pd.read_sql_query(query, conn)
    print(df)

    print("\nChecking simulation_trades (recent):")
    query_trades = "SELECT token_symbol, action, amount, price, status, timestamp, pnl_percentage FROM simulation_trades ORDER BY id DESC LIMIT 5"
    df_trades = pd.read_sql_query(query_trades, conn)
    print(df_trades)

except Exception as e:
    print(f"Error: {e}")
finally:
    conn.close()
