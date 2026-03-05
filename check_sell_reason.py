import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bsc_bot", "data", "bsc_bot.db")

def check_sell_reason():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT * FROM simulation_trades WHERE token_address = '0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82'")
    columns = [description[0] for description in cursor.description]
    for row in cursor.fetchall():
        print(dict(zip(columns, row)))
        
    conn.close()

if __name__ == "__main__":
    check_sell_reason()
