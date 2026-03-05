import sqlite3
import os

DB_PATH = "d:/workSpace/tugou/bsc_bot/data/bsc_bot.db"

def check_positions():
    if not os.path.exists(DB_PATH):
        print(f"DB not found at {DB_PATH}")
        return

    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        print(f"--- Checking Positions in {DB_PATH} ---")
        cursor.execute("SELECT token_name, status, current_price, pnl_percentage FROM simulation_positions")
        rows = cursor.fetchall()
        
        if not rows:
            print("No positions found.")
        else:
            for row in rows:
                print(f"Position: {row}")
                
        conn.close()
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    check_positions()
