import sqlite3
import os

db_path = r'd:\workSpace\tugou\bsc_bot\data\bsc_bot.db'

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    tables_to_check = [
        'simulation_positions', 
        'simulation_trades', 
        'daily_stats',
        'pairs',
        'deployer_history'
    ]
    
    print("Verifying database is empty...")
    for table_name in tables_to_check:
        cursor.execute(f"SELECT count(*) FROM {table_name}")
        count = cursor.fetchone()[0]
        print(f"{table_name}: {count} rows")
        
    conn.close()

except Exception as e:
    print(f"Error: {e}")
