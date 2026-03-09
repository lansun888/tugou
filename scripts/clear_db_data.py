import sqlite3
import os

db_path = r'd:\workSpace\tugou\bsc_bot\data\bsc_bot.db'

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # List all tables
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = cursor.fetchall()
    print(f"Tables found: {[t[0] for t in tables]}")
    
    # Tables to clear - extended list based on previous run
    tables_to_clear = [
        'simulation_positions', 
        'simulation_trades', 
        'daily_stats',
        'positions', 
        'trade_history', 
        'trades',
        'pairs',
        'deployer_history'
    ]
    
    for table_name in tables_to_clear:
        # Check if table exists
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
        if cursor.fetchone():
            print(f"Clearing table: {table_name}")
            cursor.execute(f"DELETE FROM {table_name}")
            # Reset sequence if exists (for auto increment IDs)
            cursor.execute(f"DELETE FROM sqlite_sequence WHERE name='{table_name}'")
            conn.commit()
            print(f"Cleared {table_name}")
        else:
            print(f"Table not found (skipping): {table_name}")
            
    conn.close()
    print("Database cleared successfully (ALL history wiped).")

except Exception as e:
    print(f"Error: {e}")
