import sqlite3
import os
import sys

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def run_analysis():
    # Try multiple possible DB paths
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    possible_paths = [
        os.path.join(base_dir, 'bsc_bot', 'data', 'bsc_bot.db'),
        os.path.join(base_dir, 'data', 'bsc_bot.db'),
        os.path.join(base_dir, 'bsc_bot.db'),
        os.path.join(base_dir, 'bsc_bot', 'bsc_bot.db'),
    ]
    
    best_db_path = None
    max_rows = -1
    best_table = ""

    for path in possible_paths:
        if not os.path.exists(path):
            continue
            
        try:
            conn = sqlite3.connect(path)
            cursor = conn.cursor()
            
            # Check simulation_trades
            rows = 0
            table = ""
            try:
                cursor.execute("SELECT count(*) FROM simulation_trades")
                c = cursor.fetchone()[0]
                if c > rows:
                    rows = c
                    table = "simulation_trades"
            except:
                pass
                
            # Check trades
            try:
                cursor.execute("SELECT count(*) FROM trades WHERE trade_type='simulation'")
                c = cursor.fetchone()[0]
                if c > rows:
                    rows = c
                    table = "trades"
            except:
                pass
                
            print(f"Checking {path}: Found {rows} rows in {table if table else 'none'}")
            
            if rows > max_rows:
                max_rows = rows
                best_db_path = path
                best_table = table
                
            conn.close()
        except Exception as e:
            print(f"Error checking {path}: {e}")

    if not best_db_path or max_rows <= 0:
        print("No valid database with simulation data found.")
        return

    print(f"\nUsing best database: {best_db_path} (Table: {best_table}, Rows: {max_rows})")
    conn = sqlite3.connect(best_db_path)
    cursor = conn.cursor()
    table_name = best_table
    
    # Inspect columns
    cursor.execute(f"PRAGMA table_info({table_name})")
    columns = [info[1] for info in cursor.fetchall()]
    print(f"Columns in {table_name}: {columns}")

    # Determine time columns
    start_time_col = "created_at"
    end_time_col = "updated_at"
    
    if "buy_time" in columns: start_time_col = "buy_time"
    if "sell_time" in columns: end_time_col = "sell_time"
    # If created_at exists but updated_at doesn't, maybe created_at is sell time?
    # Usually simulation_trades logs the SELL event.
    # If it has duration column?
    if "duration" in columns:
        pass # Use duration directly?
        
    print(f"Using time columns: Start={start_time_col}, End={end_time_col}")

    # Define the queries
    # Note: If using simulation_trades, we might not need "WHERE trade_type='simulation'" if the table is dedicated.
    # But if using trades, we need it.
    # Also, simulation_trades schema might differ. 
    # Standard schema for simulation_trades usually has pnl_percentage, pnl_bnb, created_at, updated_at.
    
    if table_name == "trades":
        where_clause = "WHERE action='sell' AND trade_type='simulation'"
    else:
        # Check if action column exists
        if "action" in columns:
            where_clause = "WHERE action='sell'"
        else:
            where_clause = "WHERE 1=1"

    print(f"Using WHERE clause: {where_clause}")

    print(f"\n--- Analyzing Loss Distribution ({table_name}) ---")
    sql1 = f"""
    SELECT 
        CASE 
            WHEN pnl_percentage >= 0 THEN '盈利'
            WHEN pnl_percentage >= -20 THEN '小亏(0到-20%)'
            WHEN pnl_percentage >= -50 THEN '中亏(-20到-50%)'
            ELSE '大亏(>-50%)'
        END as 亏损区间,
        COUNT(*) as 次数,
        ROUND(SUM(pnl_bnb),4) as 总盈亏
    FROM {table_name}
    {where_clause}
    GROUP BY 亏损区间
    ORDER BY 总盈亏;
    """
    
    try:
        cursor.execute(sql1)
        results = cursor.fetchall()
        print(f"{'亏损区间':<15} {'次数':<8} {'总盈亏':<10}")
        print("-" * 40)
        for row in results:
            print(f"{row[0]:<15} {row[1]:<8} {row[2]:<10}")
    except Exception as e:
        print(f"Error executing SQL1: {e}")

    print(f"\n--- Analyzing Holding Duration vs PnL ({table_name}) ---")
    
    # If duration column exists, use it directly (assuming minutes or seconds?)
    # But usually duration is calculated from timestamps.
    
    # If updated_at is missing, and sell_time is missing, we might only have created_at (which is sell time?).
    # Then we need buy_time.
    # If only created_at exists, maybe it's the time the row was inserted (sell time).
    # Does it have buy_time?
    
    # Let's adjust based on columns found
    duration_exp = f"CAST((julianday({end_time_col}) - julianday({start_time_col})) * 24 * 60 AS INTEGER)"
    
    if "duration" in columns:
        # Assuming duration is in seconds
        duration_exp = "duration / 60"
        sql2 = f"""
        SELECT 
            CASE 
                WHEN {duration_exp} < 5 THEN '5分钟内'
                WHEN {duration_exp} < 30 THEN '5-30分钟'
                WHEN {duration_exp} < 60 THEN '30-60分钟'
                ELSE '60分钟以上'
            END as 持仓时长,
            COUNT(*) as 次数,
            ROUND(AVG(pnl_percentage),2) as 平均盈亏率,
            ROUND(SUM(pnl_bnb),4) as 总盈亏
        FROM {table_name}
        {where_clause}
        GROUP BY 持仓时长
        ORDER BY 总盈亏 DESC;
        """
    elif start_time_col in columns and end_time_col in columns:
         # Standard updated_at - created_at
         duration_exp = f"CAST((julianday({end_time_col}) - julianday({start_time_col})) * 24 * 60 AS INTEGER)"
         sql2 = f"""
            SELECT 
                CASE 
                    WHEN {duration_exp} < 5 THEN '5分钟内'
                    WHEN {duration_exp} < 30 THEN '5-30分钟'
                    WHEN {duration_exp} < 60 THEN '30-60分钟'
                    ELSE '60分钟以上'
                END as 持仓时长,
                COUNT(*) as 次数,
                ROUND(AVG(pnl_percentage),2) as 平均盈亏率,
                ROUND(SUM(pnl_bnb),4) as 总盈亏
            FROM {table_name}
            {where_clause}
            GROUP BY 持仓时长
            ORDER BY 总盈亏 DESC;
            """
    else:
        # Try self-join if we have action column
        if "action" in columns and "created_at" in columns:
            print("Using self-join on simulation_trades to calculate duration...")
            sql2 = f"""
            SELECT 
                CASE 
                    WHEN CAST((julianday(sell.created_at) - julianday(buy.created_at)) * 24 * 60 AS INTEGER) < 5 THEN '5分钟内'
                    WHEN CAST((julianday(sell.created_at) - julianday(buy.created_at)) * 24 * 60 AS INTEGER) < 30 THEN '5-30分钟'
                    WHEN CAST((julianday(sell.created_at) - julianday(buy.created_at)) * 24 * 60 AS INTEGER) < 60 THEN '30-60分钟'
                    ELSE '60分钟以上'
                END as 持仓时长,
                COUNT(sell.id) as 次数,
                ROUND(AVG(sell.pnl_percentage),2) as 平均盈亏率,
                ROUND(SUM(sell.pnl_bnb),4) as 总盈亏
            FROM {table_name} sell
            JOIN {table_name} buy ON sell.token_address = buy.token_address AND buy.action = 'buy'
            WHERE sell.action = 'sell'
            GROUP BY 持仓时长
            ORDER BY 总盈亏 DESC;
            """
        else:
             print(f"Cannot calculate duration: missing necessary columns")
             return
    
    try:
        cursor.execute(sql2)
        results = cursor.fetchall()
        print(f"{'持仓时长':<12} {'次数':<6} {'平均盈亏%':<10} {'总盈亏':<10}")
        print("-" * 50)
        for row in results:
            print(f"{row[0]:<12} {row[1]:<6} {row[2]:<10} {row[3]:<10}")
    except Exception as e:
        print(f"Error executing SQL2: {e}")

    conn.close()

if __name__ == "__main__":
    run_analysis()
