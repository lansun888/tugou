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

    print(f"\nUsing best database: {best_db_path} (Table: {best_table})")
    conn = sqlite3.connect(best_db_path)
    cursor = conn.cursor()
    table_name = best_table
    
    # 1. Analyze reasons for 5-minute exits
    print("\n--- Analyzing Reasons for < 5 Minute Exits ---")
    
    # We need to self-join to get duration, and we assume 'note' column holds the reason
    # If 'note' is empty, we might need to check 'status' or just rely on 'note'.
    # The previous analysis showed 'note' exists.
    
    sql1 = f"""
    SELECT 
        sell.note as close_reason, 
        COUNT(*) as 次数, 
        ROUND(AVG(sell.pnl_percentage),2) as 平均盈亏率, 
        ROUND(SUM(sell.pnl_bnb),4) as 总盈亏 
    FROM {table_name} sell
    JOIN {table_name} buy ON sell.token_address = buy.token_address AND buy.action = 'buy'
    WHERE sell.action = 'sell' 
    AND CAST((julianday(sell.created_at) - julianday(buy.created_at)) * 24 * 60 AS INTEGER) < 5 
    GROUP BY close_reason 
    ORDER BY 次数 DESC 
    """
    
    # Check if 'note' is populated
    try:
        cursor.execute(f"SELECT count(*) FROM {table_name} WHERE action='sell' AND note IS NOT NULL AND note != ''")
        note_count = cursor.fetchone()[0]
    except Exception as e:
        print(f"Error checking note column: {e}")
        note_count = 0

    if note_count == 0:
        print("Warning: 'note' column is empty. Trying to join with simulation_positions.sold_portions...")
        # This is tricky in SQLite without JSON functions enabled or complex logic.
        # But we can try to inspect simulation_positions.
        try:
             # Just list the sold_portions from simulation_positions for these tokens
             print("Fetching reasons from simulation_positions...")
             sql_alt = f"""
             SELECT 
                 sp.sold_portions,
                 sell.pnl_percentage,
                 sell.pnl_bnb
             FROM {table_name} sell
             JOIN {table_name} buy ON sell.token_address = buy.token_address AND buy.action = 'buy'
             JOIN simulation_positions sp ON sell.token_address = sp.token_address
             WHERE sell.action = 'sell' 
             AND CAST((julianday(sell.created_at) - julianday(buy.created_at)) * 24 * 60 AS INTEGER) < 5 
             """
             cursor.execute(sql_alt)
             rows = cursor.fetchall()
             
             import json
             reasons = {}
             # ... (rest of logic)
             for r in rows:
                 portions_str = r[0]
                 pnl_pct = r[1]
                 pnl_bnb = r[2]
                 try:
                     portions_list = json.loads(portions_str)
                     for p in portions_list:
                         reason = p.get('reason', 'Unknown')
                         if reason not in reasons:
                             reasons[reason] = {'count': 0, 'pnl_pct_sum': 0.0, 'pnl_bnb_sum': 0.0}
                         
                         reasons[reason]['count'] += 1
                         reasons[reason]['pnl_pct_sum'] += pnl_pct
                         reasons[reason]['pnl_bnb_sum'] += pnl_bnb
                 except Exception as e:
                     pass
                     
             print(f"{'close_reason':<30} {'次数':<8} {'平均盈亏%':<10} {'总盈亏':<10}")
             print("-" * 60)
             for reason, stats in sorted(reasons.items(), key=lambda x: x[1]['count'], reverse=True):
                 avg_pnl = stats['pnl_pct_sum'] / stats['count']
                 print(f"{reason:<30} {stats['count']:<8} {avg_pnl:<10.2f} {stats['pnl_bnb_sum']:<10.4f}")
        except Exception as e:
             print(f"Failed to parse simulation_positions: {e}")
    else:
        try:
            cursor.execute(sql1)
            results = cursor.fetchall()
            print(f"{'close_reason':<30} {'次数':<8} {'平均盈亏%':<10} {'总盈亏':<10}")
            print("-" * 60)
            for row in results:
                reason = row[0] if row[0] else "Unknown"
                print(f"{reason:<30} {row[1]:<8} {row[2]:<10} {row[3]:<10}")
        except Exception as e:
            print(f"Error executing SQL1: {e}")

    # 2. Analyze average holding time for profitable trades

    # 2. Analyze average holding time for profitable trades
    print("\n--- Analyzing Holding Time for Profitable Trades (PnL >= 0) ---")
    
    sql2 = f"""
    SELECT 
        ROUND(AVG(CAST((julianday(sell.created_at) - julianday(buy.created_at)) * 24 * 60 AS REAL)),1) as 平均持仓分钟, 
        ROUND(AVG(sell.pnl_percentage),2) as 平均盈亏率, 
        MAX(sell.pnl_percentage) as 最高盈亏率 
    FROM {table_name} sell
    JOIN {table_name} buy ON sell.token_address = buy.token_address AND buy.action = 'buy'
    WHERE sell.action = 'sell' AND sell.pnl_percentage >= 0 
    """
    
    try:
        cursor.execute(sql2)
        row = cursor.fetchone()
        if row:
            print(f"平均持仓分钟: {row[0]}")
            print(f"平均盈亏率:   {row[1]}%")
            print(f"最高盈亏率:   {row[2]}%")
    except Exception as e:
        print(f"Error executing SQL2: {e}")

    conn.close()

if __name__ == "__main__":
    run_analysis()
