import asyncio
import aiosqlite
import os

DB_PATH = os.path.join(os.getcwd(), "bsc_bot", "data", "bsc_bot.db")
TOKEN = "0x37123d4f2f3919f3c6845fe2af70d3d198888dc7"

async def check_trade():
    async with aiosqlite.connect(DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        print(f"Checking trade for {TOKEN}...")
        
        async with db.execute(f"SELECT * FROM simulation_trades WHERE token_address='{TOKEN}' ORDER BY created_at DESC") as cursor:
            rows = await cursor.fetchall()
            for row in rows:
                print("--- Trade Record ---")
                for key in row.keys():
                    print(f"{key}: {row[key]}")

        print("\nChecking Position...")
        async with db.execute(f"SELECT * FROM simulation_positions WHERE token_address='{TOKEN}'") as cursor:
            row = await cursor.fetchone()
            if row:
                for key in row.keys():
                    print(f"{key}: {row[key]}")
            else:
                print("No position found.")

if __name__ == "__main__":
    asyncio.run(check_trade())
