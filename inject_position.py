import aiosqlite
import asyncio
import time

async def inject_position():
    db_path = "d:/workSpace/tugou/bsc_bot/data/bsc_bot.db"
    token_address = "0x0E09FaBB73Bd3Ade0a17ECC321fD13a19e81cE82" # CAKE
    token_name = "CAKE"
    
    print(f"Injecting position for {token_name} ({token_address})...")
    
    async with aiosqlite.connect(db_path) as db:
        # Check if exists
        async with db.execute("SELECT * FROM simulation_positions WHERE token_address = ?", (token_address,)) as cursor:
            existing = await cursor.fetchone()
            if existing:
                print("Position already exists. Deleting...")
                await db.execute("DELETE FROM simulation_positions WHERE token_address = ?", (token_address,))
                await db.commit()
        
        # Insert
        query = """
        INSERT INTO simulation_positions (
            token_address, token_name, buy_price_bnb, buy_amount_bnb, token_amount, 
            buy_time, highest_price, sold_portions, status, buy_gas_price, current_price, pnl_percentage
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        
        # Mock values - Use a price LOWER than current market (approx 0.0021 based on logs)
        # So we are in profit or small loss
        buy_price = 0.0020 
        amount_bnb = 0.1
        token_amount = amount_bnb / buy_price
        buy_time = time.time()
        
        await db.execute(query, (
            token_address, token_name, buy_price, amount_bnb, token_amount,
            buy_time, buy_price, "[]", "active", 0, buy_price, 0.0
        ))
        await db.commit()
        print("Position injected successfully.")

if __name__ == "__main__":
    asyncio.run(inject_position())
