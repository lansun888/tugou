import sqlite3
import os

paths = [
    os.path.join("data", "bsc_bot.db"),
    os.path.join("bsc_bot", "data", "bsc_bot.db")
]

target_names = ["GIST", "Chinamaxxing", "蜡笔小新"]

for db_path in paths:
    print(f"\nChecking DB at: {db_path}")
    if not os.path.exists(db_path):
        print("File not found.")
        continue
        
    try:
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        
        tables = ["simulation_trades", "trades"]
        for table in tables:
            print(f"  --- Table: {table} ---")
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table}'")
            if not cursor.fetchone():
                print("  Table not found.")
                continue

            cursor.execute(f"PRAGMA table_info({table})")
            columns_info = cursor.fetchall()
            columns = [col[1] for col in columns_info]
            print(f"  Columns: {columns}")
            
            # Search
            where_clauses = []
            if "token_name" in columns:
                for name in target_names:
                    where_clauses.append(f"token_name LIKE '%{name}%'")
            if "token_symbol" in columns:
                for name in target_names:
                    where_clauses.append(f"token_symbol LIKE '%{name}%'")
            
            if not where_clauses:
                print("  Cannot search (missing name/symbol columns).")
                continue
                
            query = f"SELECT * FROM {table} WHERE {' OR '.join(where_clauses)}"
            cursor.execute(query)
            rows = cursor.fetchall()
            if rows:
                for row in rows:
                    print(f"  Found: {row}")
            else:
                print("  No matches.")
                
        conn.close()
    except Exception as e:
        print(f"  Error: {e}")
