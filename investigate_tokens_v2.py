import sqlite3
import os

db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
conn = sqlite3.connect(db_path)
cursor = conn.cursor()

tables = ["simulation_trades", "trades"]
target_names = ["GIST", "Chinamaxxing", "蜡笔小新"]

for table in tables:
    print(f"\n--- Table: {table} ---")
    try:
        cursor.execute(f"PRAGMA table_info({table})")
        columns_info = cursor.fetchall()
        if not columns_info:
            print("Table not found.")
            continue
            
        columns = [col[1] for col in columns_info]
        print(f"Columns: {columns}")
        
        # Build query dynamically based on available columns
        select_cols = []
        if "token_address" in columns: select_cols.append("token_address")
        if "token_name" in columns: select_cols.append("token_name")
        if "token_symbol" in columns: select_cols.append("token_symbol")
        if "action" in columns: select_cols.append("action")
        if "price_bnb" in columns: select_cols.append("price_bnb")
        if "created_at" in columns: select_cols.append("created_at")
        elif "timestamp" in columns: select_cols.append("timestamp")
        
        if not select_cols:
            print("No useful columns found.")
            continue
            
        query_cols = ", ".join(select_cols)
        
        where_clauses = []
        for name in target_names:
            if "token_name" in columns:
                where_clauses.append(f"token_name LIKE '%{name}%'")
            if "token_symbol" in columns:
                where_clauses.append(f"token_symbol LIKE '%{name}%'")
        
        if not where_clauses:
            print("Cannot search by name/symbol (columns missing).")
            # Fallback: list all recent trades if we can't search by name?
            # No, that would be too many.
            continue
            
        where_str = " OR ".join(where_clauses)
        query = f"SELECT {query_cols} FROM {table} WHERE {where_str}"
        
        print(f"Query: {query}")
        cursor.execute(query)
        rows = cursor.fetchall()
        
        if not rows:
            print("No matching records found.")
        
        for row in rows:
            print(f"Row: {row}")
            
    except Exception as e:
        print(f"Error: {e}")

conn.close()
