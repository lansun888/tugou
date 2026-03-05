import sqlite3
import os

def check_schema():
    db_path = 'tugou.db'
    if not os.path.exists(db_path):
        print(f"Error: Database file {db_path} not found.")
        return

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    
    print("Listing all tables:")
    try:
        cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
        tables = cursor.fetchall()
        for table in tables:
            print(f"- {table[0]}")
    except Exception as e:
        print(f"Error listing tables: {e}")

    print("\nChecking schemas:")
    for table_name in ['trades', 'simulation_trades', 'positions', 'simulation_positions']:
        print(f"\nSchema for '{table_name}':")
        try:
            cursor.execute(f"PRAGMA table_info({table_name})")
            columns = cursor.fetchall()
            if not columns:
                print(f"  (Table '{table_name}' does not exist or has no columns)")
            for col in columns:
                print(f"  {col}")
        except Exception as e:
            print(f"  Error checking schema for '{table_name}': {e}")
    
    conn.close()

if __name__ == "__main__":
    check_schema()
