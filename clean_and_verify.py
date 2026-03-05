import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bsc_bot", "data", "bsc_bot.db")

def clean_and_verify():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print("--- Before Cleaning ---")
    cursor.execute("SELECT token_name, status, token_address FROM simulation_positions")
    for row in cursor.fetchall():
        print(f"Position: {row}")

    # Delete SimTokens
    cursor.execute("DELETE FROM simulation_positions WHERE token_address IN ('0xSimToken1', '0xSimToken2')")
    cursor.execute("DELETE FROM simulation_trades WHERE token_address IN ('0xSimToken1', '0xSimToken2')")
    
    conn.commit()
    
    print("\n--- After Cleaning ---")
    cursor.execute("SELECT token_name, status, token_address FROM simulation_positions")
    rows = cursor.fetchall()
    for row in rows:
        print(f"Position: {row}")
        
    conn.close()
    
    # Return true if we have valid positions left
    return len(rows) > 0

if __name__ == "__main__":
    clean_and_verify()
