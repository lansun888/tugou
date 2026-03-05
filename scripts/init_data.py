import asyncio
import aiosqlite
import random
import argparse
import os
import sys
from datetime import datetime, timedelta

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Default DB path
DEFAULT_DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bsc_bot", "data", "bsc_bot.db")

def generate_random_address():
    """Generate a random valid 40-char hex address (checksummed)"""
    return "0x" + "".join(random.choices("0123456789abcdef", k=40))

async def init_data(reset=False, no_seed=False, db_path=DEFAULT_DB_PATH):
    print(f"🔌 Connecting to database: {db_path}")
    
    # Ensure directory exists
    os.makedirs(os.path.dirname(db_path), exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        if reset:
            print("🧹 Clearing existing data...")
            await db.execute("DROP TABLE IF EXISTS pairs")
            await db.execute("DROP TABLE IF EXISTS simulation_trades")
            await db.execute("DROP TABLE IF EXISTS simulation_positions")
            await db.execute("DROP TABLE IF EXISTS daily_stats")
            await db.commit()

        print("🛠️  Initializing tables...")
        
        # 1. Pairs Table
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
                analysis_result TEXT,
                price_at_discovery REAL,
                max_price_24h REAL,
                verification_status TEXT
            )
        """)

        # 2. Simulation Trades Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS simulation_trades (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_address TEXT,
                token_name TEXT,
                token_symbol TEXT,
                action TEXT,
                amount_token TEXT,
                amount_bnb TEXT,
                price_bnb TEXT,
                price_usd TEXT,
                tx_hash TEXT,
                status TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                note TEXT,
                pnl_bnb REAL DEFAULT 0,
                pnl_percentage REAL DEFAULT 0
            )
        """)
        
        # 3. Simulation Positions Table
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
                buy_gas_price INTEGER DEFAULT 0,
                current_price REAL DEFAULT 0,
                pnl_percentage REAL DEFAULT 0
            )
        """)

        # 4. Daily Stats Table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS daily_stats (
                date DATE PRIMARY KEY,
                total_trades INTEGER DEFAULT 0,
                win_count INTEGER DEFAULT 0,
                loss_count INTEGER DEFAULT 0,
                win_rate REAL DEFAULT 0,
                avg_profit_x REAL DEFAULT 0,
                avg_loss_pct REAL DEFAULT 0,
                expected_value REAL DEFAULT 0,
                max_consecutive_loss INTEGER DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                new_coins_found INTEGER DEFAULT 0,
                passed_screening INTEGER DEFAULT 0,
                actual_bought INTEGER DEFAULT 0,
                net_pnl_bnb REAL DEFAULT 0,
                best_token TEXT,
                best_pnl_pct REAL,
                worst_token TEXT,
                worst_pnl_pct REAL,
                correct_rejected INTEGER DEFAULT 0,
                false_rejected INTEGER DEFAULT 0,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.commit()

        # Check if data exists
        async with db.execute("SELECT COUNT(*) FROM pairs") as cursor:
            count = (await cursor.fetchone())[0]
            if count > 0 and not reset:
                print(f"⚠️  Database already contains {count} pairs. Use --reset to clear.")
                return

        if no_seed:
            print("🛑 Skipping dummy data seeding as requested.")
            print("✅ Database initialized and cleared.")
            return

        print("🌱 Seeding dummy data...")
        
        # Seed Pairs
        dexes = ["PancakeSwap V2", "PancakeSwap V3", "Biswap", "FourMeme"]
        statuses = ["analyzing", "safe", "risky", "bought", "rejected"]
        
        # Pre-generate some tokens for consistency across tables
        tokens = []
        for i in range(20):
            tokens.append({
                "address": generate_random_address(),
                "pair": generate_random_address(),
                "symbol": f"MEME{i}",
                "name": f"Meme Token {i}",
                "deployer": generate_random_address()
            })

        for i, token in enumerate(tokens):
            score = random.randint(30, 100)
            status = "bought" if score > 90 else ("safe" if score > 80 else "risky")
            
            # Generate realistic timestamp within last 24 hours
            discovery_time = datetime.now() - timedelta(minutes=random.randint(1, 1440))
            
            await db.execute("""
                INSERT OR REPLACE INTO pairs (
                    pair_address, target_token, token0, token1, dex_name, 
                    discovered_at, deployer, initial_liquidity, is_risky, risk_reason,
                    token_name, token_symbol, security_score, status, analysis_result,
                    price_at_discovery, check_details
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token["pair"], token["address"], "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c", token["address"], random.choice(dexes),
                discovery_time, token["deployer"], random.uniform(1.0, 100.0), score < 60,
                "Low Score" if score < 60 else None, token["name"], token["symbol"], score, status,
                "Safe" if score > 80 else "Risky", random.uniform(0.00001, 0.001), "{}"
            ))

        # Seed Positions (Active)
        # Use tokens 18 and 19 for active positions
        active_indices = [18, 19]
        for idx in active_indices:
            token = tokens[idx]
            buy_price = 0.0001
            current_price = buy_price * 1.1
            pnl_pct = 10.0
            
            await db.execute("""
                INSERT OR REPLACE INTO simulation_positions (
                    token_address, token_name, buy_price_bnb, buy_amount_bnb, 
                    token_amount, buy_time, highest_price, sold_portions, status,
                    current_price, pnl_percentage
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token["address"], token["name"], buy_price, 0.1, 1000, 
                (datetime.now() - timedelta(minutes=30)).timestamp(), 
                buy_price * 1.2, "[]", "active",
                current_price, pnl_pct
            ))

        # Seed Trades (History)
        for i in range(10):
            token = tokens[i]
            action = "BUY"
            pnl = 0
            if i % 2 != 0:
                action = "SELL"
                pnl = random.uniform(-0.05, 0.2)
            
            await db.execute("""
                INSERT INTO simulation_trades (
                    token_address, token_symbol, token_name, action, amount_token, 
                    amount_bnb, price_bnb, tx_hash, status, created_at, pnl_bnb
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                token["address"], token["symbol"], token["name"], action, "1000",
                "0.1", "0.0001", generate_random_address(), "success", 
                datetime.now() - timedelta(hours=random.randint(1, 48)), pnl
            ))

        await db.commit()
        print("✅ Data initialization complete!")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Initialize database with dummy data")
    parser.add_argument("--reset", action="store_true", help="Clear existing data before initializing")
    parser.add_argument("--no-seed", action="store_true", help="Do not seed dummy data (create empty tables)")
    args = parser.parse_args()
    
    asyncio.run(init_data(reset=args.reset, no_seed=args.no_seed))
