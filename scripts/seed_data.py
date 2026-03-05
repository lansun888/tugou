import asyncio
import aiosqlite
import json
import time
import random
from datetime import datetime, timedelta

DB_PATH = "d:/workSpace/tugou/bsc_bot/data/bsc_bot.db"

async def seed_data():
    print(f"Connecting to database at {DB_PATH}...")
    async with aiosqlite.connect(DB_PATH) as db:
        # 1. Create Tables (Just in case)
        print("Ensuring tables exist...")
        
        # Pairs Table
        # Note: We assume the table is created by the application with the correct schema
        # but we can try to create it if it doesn't exist with the CORRECT schema
        await db.execute("""
            CREATE TABLE IF NOT EXISTS pairs (
                pair_address TEXT PRIMARY KEY,
                token0 TEXT,
                token1 TEXT,
                target_token TEXT,
                dex_name TEXT,
                discovered_at TIMESTAMP,
                deployer TEXT,
                initial_liquidity REAL,
                is_risky BOOLEAN,
                risk_reason TEXT,
                token_name TEXT,
                token_symbol TEXT,
                security_score INTEGER DEFAULT 0,
                check_details TEXT DEFAULT '{}',
                status TEXT DEFAULT 'analyzing',
                analysis_result TEXT
            )
        """)

        # Simulation Trades Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS simulation_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT,
                token_symbol TEXT,
                action TEXT,
                amount TEXT,
                price TEXT,
                tx_hash TEXT,
                status TEXT,
                timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
                pnl_percentage REAL,
                pnl_bnb REAL
            )
        """)
        
        # Simulation Positions Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS simulation_positions (
                token_address TEXT PRIMARY KEY,
                token_name TEXT,
                buy_price_bnb REAL,
                buy_amount_bnb REAL,
                token_amount REAL,
                buy_time REAL,
                highest_price REAL,
                sold_portions TEXT,
                status TEXT,
                buy_gas_price INTEGER DEFAULT 0
            )
        """)

        await db.commit()

        # 2. Clear existing data (Optional, but good for reset)
        # await db.execute("DELETE FROM pairs")
        # await db.execute("DELETE FROM simulation_trades")
        # await db.execute("DELETE FROM simulation_positions")
        # await db.commit()

        # 3. Insert Dummy Pairs (Discoveries)
        print("Inserting dummy pairs...")
        pairs_data = []
        for i in range(5):
            pair_addr = f"0x{random.randint(100000, 999999)}pair{i}"
            token_addr = f"0x{random.randint(100000, 999999)}token{i}"
            symbol = f"MOCK{i}"
            deployer = f"0x{random.randint(100000, 999999)}deployer"
            score = random.randint(60, 95)
            status = "analyzing" if i < 2 else ("bought" if score > 80 else "rejected")
            risk = "Low Liquidity" if score < 70 else "None"
            
            await db.execute("""
                INSERT OR IGNORE INTO pairs (
                    pair_address, target_token, token_symbol, deployer, dex_name, 
                    security_score, analysis_result, check_details, status, risk_reason,
                    token0, token1, discovered_at, initial_liquidity, is_risky, token_name
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pair_addr, token_addr, symbol, deployer, "PancakeSwap V2", 
                score, status.upper(), "{}", status, risk,
                "0xWBNB", token_addr, datetime.now(), 10.0, False, f"Mock Token {i}"
            ))
        
        # 4. Insert Dummy Trades (Simulation Stats)
        print("Inserting dummy trades...")
        now = datetime.now()
        for i in range(10):
            trade_time = now - timedelta(hours=random.randint(1, 48))
            action = "BUY" if i % 2 == 0 else "SELL"
            pnl = random.uniform(-0.05, 0.1) if action == "SELL" else 0
            pnl_pct = pnl * 1000
            
            await db.execute("""
                INSERT INTO simulation_trades (token_address, token_symbol, action, amount, price, tx_hash, status, timestamp, pnl_percentage, pnl_bnb)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (f"0xToken{i}", f"TKN{i}", action, "1000", "0.0001", f"0xTx{i}", "success", trade_time, pnl_pct, pnl))

        # 5. Insert Dummy Positions
        print("Inserting dummy positions...")
        pos_time = time.time() - 3600 # 1 hour ago
        buy_price = 0.001
        
        # Position 1: Profitable
        await db.execute("""
            INSERT OR REPLACE INTO simulation_positions (token_address, token_name, buy_price_bnb, buy_amount_bnb, token_amount, buy_time, highest_price, sold_portions, status, buy_gas_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("0xSimToken1", "SIM1", buy_price, 0.1, 100, pos_time, buy_price * 1.5, "[]", "active", 5000000000))
        
        # Position 2: Losing
        await db.execute("""
            INSERT OR REPLACE INTO simulation_positions (token_address, token_name, buy_price_bnb, buy_amount_bnb, token_amount, buy_time, highest_price, sold_portions, status, buy_gas_price)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, ("0xSimToken2", "SIM2", buy_price, 0.1, 100, pos_time - 7200, buy_price, "[]", "active", 5000000000))

        await db.commit()
        print("Seed data inserted successfully!")

if __name__ == "__main__":
    asyncio.run(seed_data())
