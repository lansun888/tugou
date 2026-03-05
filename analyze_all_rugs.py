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

    # Query ALL positions
    query = """
    SELECT 
        sp.token_name,
        sp.token_address,
        sp.buy_time,
        sp.sold_portions,
        sp.status,
        sp.pnl_percentage,
        p.initial_liquidity,
        p.security_score
    FROM simulation_positions sp
    LEFT JOIN pairs p ON sp.token_address = p.target_token
    ORDER BY sp.buy_time DESC
    """
    
    try:
        cursor.execute(query)
        rows = cursor.fetchall()
        
        results = []
        
        for row in rows:
            sold_portions = []
            try:
                if row['sold_portions']:
                    sold_portions = json.loads(row['sold_portions'])
            except:
                pass
            
            # Determine if rugged/crashed
            is_rugged = False
            rug_time = None
            rug_reason = None
            
            # Determine if profitable
            is_profitable = False
            profit_reason = None
            
            # Find first rug/crash event
            for sale in sold_portions:
                reason = sale.get('reason', '').lower()
                if 'rug' in reason or 'crash' in reason:
                    is_rugged = True
                    rug_time = sale.get('time')
                    rug_reason = sale.get('reason')
                    break # Use the first rug event
            
            # Check for profit
            # If any sale was take_profit, or overall PnL > 0 (and not rugged?)
            # Actually, even if rugged later, if we took profit earlier, it counts as a profitable trade?
            # User asks "Any profitable trades?".
            for sale in sold_portions:
                reason = sale.get('reason', '').lower()
                if 'take_profit' in reason:
                    is_profitable = True
                    profit_reason = sale.get('reason')
                    break
            
            # If no specific profit sale, check overall PnL
            if not is_profitable and row['pnl_percentage'] > 0:
                is_profitable = True
                profit_reason = "Positive PnL"

            buy_time = row['buy_time']
            hold_minutes = 0.0
            
            if rug_time:
                hold_minutes = (rug_time - buy_time) / 60
            elif sold_portions:
                # If not rugged, but sold, use last sell time
                last_sell_time = sold_portions[-1].get('time')
                if last_sell_time:
                    hold_minutes = (last_sell_time - buy_time) / 60
            else:
                # Still holding? Use current time
                hold_minutes = (datetime.now().timestamp() - buy_time) / 60

            results.append({
                'name': row['token_name'],
                'buy_time': buy_time,
                'hold_minutes': hold_minutes,
                'score': row['security_score'] if row['security_score'] is not None else 0,
                'liq': row['initial_liquidity'] if row['initial_liquidity'] is not None else 0,
                'is_rugged': is_rugged,
                'rug_reason': rug_reason,
                'is_profitable': is_profitable,
                'pnl': row['pnl_percentage'],
                'status': row['status']
            })

        # --- Report ---
        
        # Filter for rugged/crashed tokens
        rugged_tokens = [r for r in results if r['is_rugged']]
        
        print(f"\nTotal Positions: {len(results)}")
        print(f"Total Rugged/Crashed: {len(rugged_tokens)}")
        
        print(f"\n{'Token':<15} | {'Buy Time':<16} | {'Hold(min)':<10} | {'Liq':<6} | {'Score':<5} | {'Reason'}")
        print("-" * 90)
        
        for r in rugged_tokens[:20]: # Limit to 20 as requested
            bt = datetime.fromtimestamp(r['buy_time']).strftime('%m-%d %H:%M')
            print(f"{r['name'][:15]:<15} | {bt:<16} | {r['hold_minutes']:<10.1f} | {r['liq']:<6.1f} | {r['score']:<5} | {r['rug_reason']}")

        # Q1: 30-60 min distribution
        rugs_30_60 = [r for r in rugged_tokens if 30 <= r['hold_minutes'] <= 60]
        rugs_lt_30 = [r for r in rugged_tokens if r['hold_minutes'] < 30]
        rugs_gt_60 = [r for r in rugged_tokens if r['hold_minutes'] > 60]
        
        print(f"\n1. Time Distribution for Rugs ({len(rugged_tokens)} total):")
        print(f"   < 30 min:  {len(rugs_lt_30)}")
        print(f"   30-60 min: {len(rugs_30_60)}")
        print(f"   > 60 min:  {len(rugs_gt_60)}")
        
        # Q2: Score 80 Win Rate
        score_80_tokens = [r for r in results if r['score'] == 80]
        wins_80 = [r for r in score_80_tokens if r['is_profitable']]
        
        print(f"\n2. Score 80 Analysis:")
        print(f"   Total Score 80 Tokens: {len(score_80_tokens)}")
        if score_80_tokens:
            win_rate = len(wins_80) / len(score_80_tokens) * 100
            print(f"   Win Rate (Profitable): {win_rate:.1f}% ({len(wins_80)}/{len(score_80_tokens)})")
        else:
            print("   No Score 80 tokens found.")
            
        # Q3: Profitable Trades
        all_profitable = [r for r in results if r['is_profitable']]
        print(f"\n3. Profitable Trades: {len(all_profitable)}")
        for p in all_profitable[:10]:
            print(f"   - {p['name']} (PnL: {p['pnl']}%)")

    except Exception as e:
        print(f"Error: {e}")
        import traceback
        traceback.print_exc()

    conn.close()

if __name__ == "__main__":
    analyze()
