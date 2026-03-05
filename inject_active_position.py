import sqlite3
import time
import json

DB_PATH = "d:/workSpace/tugou/bsc_bot/data/bsc_bot.db"
CAKE_ADDRESS = "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82"

def inject_position():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    print(f"Injecting active position for CAKE ({CAKE_ADDRESS})...")
    
    # Inject active position
    cursor.execute("""
        INSERT OR REPLACE INTO simulation_positions 
        (token_address, token_name, buy_price_bnb, buy_amount_bnb, token_amount, buy_time, highest_price, sold_portions, status, buy_gas_price, current_price, pnl_percentage)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        CAKE_ADDRESS, 
        "PancakeSwap Token", 
        0.001, # buy_price
        0.1,   # buy_amount
        100.0, # token_amount
        time.time(), 
        0.001, # highest_price
        json.dumps([]), # sold_portions
        "active", # status
        5,     # buy_gas_price
        0.0,   # current_price (will be updated by monitor)
        0.0    # pnl
    ))
    
    conn.commit()
    conn.close()
    print("Injection complete.")

if __name__ == "__main__":
    inject_position()
