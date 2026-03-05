import sqlite3
import os

# DB_PATH
db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
if not os.path.exists(db_path):
    print(f"Database not found at {db_path}")
    exit(1)

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

target_names = ["GIST", "Chinamaxxing", "蜡笔小新"]
tables = ["trades", "simulation_trades"]

print(f"Searching for tokens: {target_names} in {tables}...\n")

for table in tables:
    print(f"--- Table: {table} ---")
    try:
        # Check if table exists
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
        if not cursor.fetchone():
            print(f"Table {table} does not exist.")
            continue
            
        # Get columns to find indices
        cursor.execute(f"PRAGMA table_info({table})")
        columns = [col[1] for col in cursor.fetchall()]
        
        # Build query
        # We want: token_address, token_name, action, price_bnb, created_at/timestamp
        select_cols = ["token_address", "token_name", "action", "price_bnb"]
        if "created_at" in columns:
            select_cols.append("created_at")
        elif "timestamp" in columns:
            select_cols.append("timestamp")
        else:
            select_cols.append("'Unknown Time'")
            
        if "note" in columns:
            select_cols.append("note")
        
        query = f"SELECT {', '.join(select_cols)} FROM {table} WHERE "
        conditions = []
        for name in target_names:
            conditions.append(f"token_name LIKE '%{name}%'")
            conditions.append(f"token_symbol LIKE '%{name}%'")
        
        query += " OR ".join(conditions)
        query += " ORDER BY " + ("created_at" if "created_at" in columns else "timestamp")
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        if not rows:
            print("No records found.")
        
        for row in rows:
            print(f"Found: {row}")
            
    except Exception as e:
        print(f"Error querying {table}: {e}")

conn.close()
