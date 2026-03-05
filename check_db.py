import aiosqlite
import asyncio

async def check_db():
    db_path = "d:/workSpace/tugou/bsc_bot/data/bsc_bot.db"
    async with aiosqlite.connect(db_path) as db:
        async with db.execute("SELECT * FROM simulation_positions") as cursor:
            rows = await cursor.fetchall()
            print(f"Positions in DB: {len(rows)}")
            for row in rows:
                print(row)

if __name__ == "__main__":
    asyncio.run(check_db())
