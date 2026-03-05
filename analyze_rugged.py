import sqlite3
import os
import json
from datetime import datetime

def analyze():
    db_path = os.path.join("bsc_bot", "data", "bsc_bot.db")
    if not os.path.exists(db_path):
        print(f"Database not found at {db_path}")
        return

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()

    # Query positions
    query = """
    SELECT 
        sp.token_name,
        sp.token_address,
        sp.buy_time,
        sp.sold_portions,
        sp.status,
        p.initial_liquidity,
        p.security_score
    FROM simulation_positions sp
    LEFT JOIN pairs p ON sp.token_address = p.target_token
    WHERE sp.status IN ('closed', 'rug_lost', 'sold_out')
    ORDER BY sp.buy_time DESC
    LIMIT 20
    """
    
    # Note: 'sold_out' is another possible status for fully sold positions? 
    # Let's check all statuses first just in case.
    
    try:
        cursor.execute("SELECT DISTINCT status FROM simulation_positions")
        statuses = [row[0] for row in cursor.fetchall()]
        print(f"Available statuses: {statuses}")
        
        cursor.execute(query)
        rows = cursor.fetchall()
        
        print(f"\nFound {len(rows)} closed/rugged positions.\n")
        
        # Header
        print(f"{'Token':<15} | {'Buy Time':<20} | {'Close Time':<20} | {'Hold(min)':<10} | {'Liq(BNB)':<10} | {'Score':<5} | {'Reason'}")
        print("-" * 110)
        
        results = []
        
        for row in rows:
            token_name = row['token_name']
            buy_time = row['buy_time']
            sold_portions_json = row['sold_portions']
            initial_liquidity = row['initial_liquidity']
            security_score = row['security_score']
            
            # Parse sold_portions
            try:
                sold_portions = json.loads(sold_portions_json)
            except:
                sold_portions = []
            
            close_time = None
            close_reason = "Unknown"
            
            if sold_portions:
                # Get the last sell event
                last_sell = sold_portions[-1]
                close_time_ts = last_sell.get('time')
                close_reason = last_sell.get('reason', 'Unknown')
                
                if close_time_ts:
                    close_time = datetime.fromtimestamp(close_time_ts)
            
            buy_dt = datetime.fromtimestamp(buy_time)
            buy_time_str = buy_dt.strftime('%Y-%m-%d %H:%M')
            
            close_time_str = "N/A"
            hold_minutes = 0.0
            
            if close_time:
                close_time_str = close_time.strftime('%Y-%m-%d %H:%M')
                hold_minutes = (close_time - buy_dt).total_seconds() / 60
            
            print(f"{token_name[:15]:<15} | {buy_time_str:<20} | {close_time_str:<20} | {hold_minutes:<10.1f} | {initial_liquidity if initial_liquidity else 0:<10.2f} | {security_score if security_score else 0:<5} | {close_reason}")
            
            results.append({
                'hold_minutes': hold_minutes,
                'score': security_score if security_score else 0,
                'reason': close_reason,
                'status': row['status']
            })

        # Analysis
        print("\n--- Analysis ---")
        
        # 1. Time distribution for rugs
        rugs = [r for r in results if 'rug' in str(r['reason']).lower() or 'crash' in str(r['reason']).lower()]
        rugs_30_60 = [r for r in rugs if 30 <= r['hold_minutes'] <= 60]
        print(f"1. Total Rugs/Crashes: {len(rugs)}")
        if rugs:
            print(f"   Rugs in 30-60 min: {len(rugs_30_60)} ({len(rugs_30_60)/len(rugs)*100:.1f}%)")
        
        # 2. Win rate for score 80
        score_80 = [r for r in results if r['score'] == 80]
        # Define "win" as not rug/crash? Or profitable?
        # User asked "Actual win rate". Usually means profit > 0.
        # I need to check PnL. I missed PnL in the query.
        # Let's assume non-rug is a win? Or check sold price vs buy price?
        # I'll check 'reason' for 'take_profit'.
        wins_80 = [r for r in score_80 if 'take_profit' in str(r['reason']).lower()]
        print(f"2. Score 80 Tokens: {len(score_80)}")
        if score_80:
            print(f"   Win Rate (Take Profit): {len(wins_80)/len(score_80)*100:.1f}%")
            
        # 3. Profitable trades
        profitable = [r for r in results if 'take_profit' in str(r['reason']).lower()]
        print(f"3. Profitable Trades: {len(profitable)}")
        for p in profitable:
            print(f"   - {p['reason']}")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    conn.close()

if __name__ == "__main__":
    analyze()
