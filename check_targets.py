import sqlite3
import os
import json
from datetime import datetime

def analyze():
    db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query specific tokens
    target_names = ["GIST", "Chinamaxxing", "蜡笔小新"]
    placeholders = ",".join("?" for _ in target_names)
    
    query = f"""
    SELECT 
        sp.token_name,
        sp.token_address,
        sp.buy_time,
        sp.sold_portions,
        sp.status,
        sp.current_price,
        sp.pnl_percentage
    FROM simulation_positions sp
    WHERE sp.token_name IN ({placeholders})
    """
    
    try:
        cursor.execute(query, target_names)
        rows = cursor.fetchall()
        
        print(f"\nFound {len(rows)} target positions.\n")
        
        for row in rows:
            print(f"Token: {row['token_name']}")
            print(f"Status: {row['status']}")
            print(f"Buy Time: {datetime.fromtimestamp(row['buy_time'])}")
            print(f"Sold Portions: {row['sold_portions']}")
            print(f"Current Price: {row['current_price']}")
            print(f"PnL: {row['pnl_percentage']}%")
            print("-" * 30)

    except Exception as e:
        print(f"Error: {e}")

    conn.close()

if __name__ == "__main__":
    analyze()
