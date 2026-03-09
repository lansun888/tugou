import sqlite3
import os

db_path = r'd:\workSpace\tugou\bsc_bot\data\bsc_bot.db'

try:
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    # Get column names
    cursor.execute("PRAGMA table_info(simulation_positions)")
    columns = [row[1] for row in cursor.fetchall()]
    print(f"Columns: {columns}")
    
    # Determine the correct column name for platform/dex
    platform_col = 'platform' if 'platform' in columns else 'dex_name'
    print(f"Using platform column: {platform_col}")
    
    # Execute query
    query = f"SELECT token_name, token_address, pair_address, {platform_col} FROM simulation_positions WHERE status='active'"
    print(f"Executing: {query}")
    
    cursor.execute(query)
    rows = cursor.fetchall()
    
    if not rows:
        print("No active positions found.")
    else:
        print(f"{'Token Name':<20} | {'Token Address':<42} | {'Pair Address':<42} | {'Platform':<10}")
        print("-" * 120)
        for row in rows:
            token_name = str(row[0])[:20]
            token_addr = str(row[1])
            pair_addr = str(row[2]) if row[2] else "None"
            platform = str(row[3])
            print(f"{token_name:<20} | {token_addr:<42} | {pair_addr:<42} | {platform:<10}")
            
    conn.close()

except Exception as e:
    print(f"Error: {e}")
