import asyncio
import os
import sys

# Ensure project root is in path
sys.path.append(os.getcwd())

from web.database_helper import DatabaseHelper

async def test_stats():
    db_helper = DatabaseHelper()
    
    print("Fetching daily stats (simulation)...")
    stats = await db_helper.get_daily_stats(7, "simulation_trades", "simulation_positions")
    
    print(f"Found {len(stats)} days of stats")
    for day in stats:
        print(f"Date: {day['day']}")
        print(f"  Total Trades: {day.get('total_trades')}")
        print(f"  Sell Count: {day.get('sell_count')}")
        print(f"  Win Count: {day.get('win_count')}")
        print(f"  Loss Count: {day.get('loss_count')}")
        print(f"  Profit: {day.get('profit_bnb')}")
        print(f"  Loss: {day.get('loss_bnb')}")
        print(f"  Total PnL: {day.get('total_pnl_bnb')}")

if __name__ == "__main__":
    asyncio.run(test_stats())
