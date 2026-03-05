import asyncio
import logging
import json
import os
import time
from datetime import datetime, timedelta
import aiosqlite
from web3 import AsyncWeb3
import numpy as np
from typing import Dict, List, Optional, Any

logger = logging.getLogger(__name__)

import aiohttp

class PerformanceAnalyzer:
    def __init__(self, db_path="./data/bsc_bot.db", config=None, w3=None):
        self.db_path = db_path
        self.config = config or {}
        self.w3 = w3

    async def init_db(self):
        """Initialize performance related tables"""
        async with aiosqlite.connect(self.db_path) as db:
            # Daily Stats Table
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
            
            # Migration for pairs table to support verification
            try:
                # check if columns exist
                async with db.execute("PRAGMA table_info(pairs)") as cursor:
                    columns = [row[1] for row in await cursor.fetchall()]
                    
                if "verification_status" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN verification_status TEXT") # 'correct_reject', 'false_positive', 'scam_caught', 'scam_missed'
                if "price_at_discovery" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN price_at_discovery REAL")
                if "max_price_24h" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN max_price_24h REAL")
            except Exception as e:
                logger.warning(f"Migration warning: {e}")
                
            # Migration for daily_stats
            try:
                async with db.execute("PRAGMA table_info(daily_stats)") as cursor:
                    columns = [row[1] for row in await cursor.fetchall()]
                    
                if "correct_rejected" not in columns:
                    await db.execute("ALTER TABLE daily_stats ADD COLUMN correct_rejected INTEGER DEFAULT 0")
                if "false_rejected" not in columns:
                    await db.execute("ALTER TABLE daily_stats ADD COLUMN false_rejected INTEGER DEFAULT 0")
                
                # Add missing stats columns
                if "win_rate" not in columns:
                    await db.execute("ALTER TABLE daily_stats ADD COLUMN win_rate REAL DEFAULT 0")
                if "avg_profit_x" not in columns:
                    await db.execute("ALTER TABLE daily_stats ADD COLUMN avg_profit_x REAL DEFAULT 0")
                if "avg_loss_pct" not in columns:
                    await db.execute("ALTER TABLE daily_stats ADD COLUMN avg_loss_pct REAL DEFAULT 0")
                if "expected_value" not in columns:
                    await db.execute("ALTER TABLE daily_stats ADD COLUMN expected_value REAL DEFAULT 0")
                if "max_consecutive_loss" not in columns:
                    # Check if max_con_loss exists (from previous init script)
                    if "max_con_loss" in columns:
                         # SQLite doesn't support RENAME COLUMN easily in older versions, so we might just add new one
                         await db.execute("ALTER TABLE daily_stats ADD COLUMN max_consecutive_loss INTEGER DEFAULT 0")
                    else:
                        await db.execute("ALTER TABLE daily_stats ADD COLUMN max_consecutive_loss INTEGER DEFAULT 0")

            except Exception as e:
                logger.warning(f"Daily stats migration warning: {e}")
                
            await db.commit()

    async def calculate_daily_stats(self, target_date: str = None):
        """
        Calculate stats for a specific date (YYYY-MM-DD).
        If None, defaults to yesterday (for full day stats).
        """
        if not target_date:
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
            
        logger.info(f"Calculating stats for {target_date}...")
        
        start_ts = datetime.strptime(target_date, "%Y-%m-%d").timestamp()
        end_ts = start_ts + 86400
        
        stats = {
            "date": target_date,
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "net_pnl_bnb": 0.0,
            "profits": [],
            "losses": [],
            "max_con_loss": 0,
            "drawdowns": []
        }

        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # 1. Get Trades & PnL (Only fully closed trades or explicit sold portions)
            # Filter logic: We only care about realized PnL from sold portions.
            # If a position is still active (no sold portions), it contributes 0 to realized PnL.
            async with db.execute("SELECT * FROM positions WHERE sold_portions IS NOT NULL AND sold_portions != '[]'") as cursor:
                rows = await cursor.fetchall()
                
                daily_pnl_records = []
                
                for row in rows:
                    try:
                        row_dict = dict(row)
                        sold_data = json.loads(row_dict['sold_portions'])
                        token_symbol = row_dict.get('token_symbol') or row_dict.get('token_name') or 'Unknown'
                        buy_price_bnb = float(row_dict.get('buy_price_bnb', 0) or 0)
                        
                        for record in sold_data:
                            # Check date
                            r_time = record.get('time', 0)
                            r_date = datetime.fromtimestamp(r_time).strftime("%Y-%m-%d")
                            
                            if r_date == target_date:
                                # Found a trade for this day
                                pnl = float(record.get('pnl', 0))
                                bnb_got = float(record.get('bnb_got', 0))
                                
                                # Calculate PnL %
                                # Cost = Revenue - PnL
                                cost = bnb_got - pnl
                                if cost > 0:
                                    pnl_pct = (pnl / cost) * 100
                                else:
                                    # Fallback if cost is weird (e.g. transfer?)
                                    # Try to use buy_price * amount
                                    amount = float(record.get('amount', 0))
                                    if amount > 0 and buy_price_bnb > 0:
                                        cost_est = amount * buy_price_bnb
                                        pnl_pct = (pnl / cost_est) * 100
                                    else:
                                        pnl_pct = 0
                                
                                daily_pnl_records.append({
                                    'pnl': pnl,
                                    'pnl_pct': pnl_pct,
                                    'symbol': token_symbol,
                                    'time': r_time
                                })
                    except Exception as e:
                        logger.error(f"Error parsing position row: {e}")
                        continue
                
                # Sort by time
                daily_pnl_records.sort(key=lambda x: x['time'])
                
                # Calculate Metrics
                current_con_loss = 0
                max_con_loss = 0
                cumulative_pnl = 0
                min_cumulative_pnl = 0 # for drawdown
                
                for record in daily_pnl_records:
                    stats["total_trades"] += 1
                    stats["net_pnl_bnb"] += record['pnl']
                    
                    cumulative_pnl += record['pnl']
                    if cumulative_pnl < min_cumulative_pnl:
                        min_cumulative_pnl = cumulative_pnl
                    
                    if record['pnl'] > 0:
                        stats["win_count"] += 1
                        stats["profits"].append(record['pnl_pct']) # Using percentage for Avg Profit X
                        current_con_loss = 0
                    else:
                        stats["loss_count"] += 1
                        stats["losses"].append(record['pnl_pct'])
                        current_con_loss += 1
                        if current_con_loss > max_con_loss:
                            max_con_loss = current_con_loss
                            
                stats["max_con_loss"] = max_con_loss
                stats["max_drawdown"] = min_cumulative_pnl
                
            # 2. Get Discovery Stats
            # Count pairs discovered on this date
            async with db.execute(
                "SELECT COUNT(*) FROM pairs WHERE date(discovered_at) = ?", 
                (target_date,)
            ) as cursor:
                stats["new_coins_found"] = (await cursor.fetchone())[0]
                
            # Count passed screening (status != 'rejected')
            async with db.execute(
                "SELECT COUNT(*) FROM pairs WHERE date(discovered_at) = ? AND status != 'rejected'", 
                (target_date,)
            ) as cursor:
                stats["passed_screening"] = (await cursor.fetchone())[0]
                
            # Count actual bought (status = 'bought' or exists in trades)
            async with db.execute(
                "SELECT COUNT(*) FROM pairs WHERE date(discovered_at) = ? AND status = 'bought'", 
                (target_date,)
            ) as cursor:
                stats["actual_bought"] = (await cursor.fetchone())[0]

            # Filter Effectiveness Stats
            # Correct Rejects
            async with db.execute(
                "SELECT COUNT(*) FROM pairs WHERE date(discovered_at) = ? AND verification_status = 'correct_reject'", 
                (target_date,)
            ) as cursor:
                stats["correct_rejected"] = (await cursor.fetchone())[0]
                
            # False Rejects (Missed Opportunities)
            async with db.execute(
                "SELECT COUNT(*) FROM pairs WHERE date(discovered_at) = ? AND verification_status = 'false_positive'", 
                (target_date,)
            ) as cursor:
                stats["false_rejected"] = (await cursor.fetchone())[0]

        # Derived Stats
        win_rate = (stats["win_count"] / stats["total_trades"]) if stats["total_trades"] > 0 else 0
        avg_profit = np.mean(stats["profits"]) if stats["profits"] else 0
        avg_loss = np.mean(stats["losses"]) if stats["losses"] else 0 # negative value
        
        # Expectancy = (Win% * Avg_Win_Amt) - (Loss% * Avg_Loss_Amt)
        # Or simply: Total PnL / Total Trades (in BNB)
        expected_value = (stats["net_pnl_bnb"] / stats["total_trades"]) if stats["total_trades"] > 0 else 0
        
        best_token = ""
        best_pnl = -999
        worst_token = ""
        worst_pnl = 999
        
        if daily_pnl_records:
            sorted_by_pnl = sorted(daily_pnl_records, key=lambda x: x['pnl_pct'])
            worst = sorted_by_pnl[0]
            best = sorted_by_pnl[-1]
            best_token = best['symbol']
            best_pnl = best['pnl_pct']
            worst_token = worst['symbol']
            worst_pnl = worst['pnl_pct']

        # Recalculate Expected Value based on Win/Loss avg
        expected_value = (win_rate * avg_profit) + ((1-win_rate) * avg_loss)

        # Update stats dict with derived values
        stats["win_rate"] = win_rate
        stats["avg_profit_x"] = avg_profit
        stats["avg_loss_pct"] = avg_loss
        stats["expected_value"] = expected_value
        stats["best_token"] = best_token
        stats["best_pnl_pct"] = best_pnl
        stats["worst_token"] = worst_token
        stats["worst_pnl_pct"] = worst_pnl

        # Store in DB
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("""
                INSERT OR REPLACE INTO daily_stats (
                    date, total_trades, win_count, loss_count, win_rate, 
                    avg_profit_x, avg_loss_pct, expected_value, max_consecutive_loss, 
                    max_drawdown, new_coins_found, passed_screening, actual_bought, 
                    net_pnl_bnb, best_token, best_pnl_pct, worst_token, worst_pnl_pct,
                    correct_rejected, false_rejected
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                stats["date"],
                stats["total_trades"],
                stats["win_count"],
                stats["loss_count"],
                stats["win_rate"],
                stats["avg_profit_x"],
                stats["avg_loss_pct"],
                stats["expected_value"],
                stats["max_con_loss"],
                stats["max_drawdown"],
                stats["new_coins_found"],
                stats["passed_screening"],
                stats["actual_bought"],
                stats["net_pnl_bnb"],
                stats["best_token"],
                stats["best_pnl_pct"],
                stats["worst_token"],
                stats["worst_pnl_pct"],
                stats.get("correct_rejected", 0),
                stats.get("false_rejected", 0)
            ))
            await db.commit()
            
        return stats


    async def analyze_filter_effectiveness(self):
        """
        Check rejected coins to see if they were correctly rejected.
        Check bought coins to see if they were rugs/honeypots.
        """
        if not self.w3:
            logger.warning("Web3 not initialized, skipping on-chain verification")
            return

        logger.info("Analyzing filter effectiveness...")
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # 1. Check Rejected Coins (last 24h)
            # Fetch pairs rejected in last 24h that haven't been verified
            yesterday = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")
            
            async with db.execute("""
                SELECT * FROM pairs 
                WHERE status = 'rejected' 
                AND discovered_at > ? 
                AND (verification_status IS NULL OR verification_status = '')
            """, (yesterday,)) as cursor:
                rejected_pairs = await cursor.fetchall()
                
            for pair in rejected_pairs:
                try:
                    # Check current liquidity
                    pair_addr = AsyncWeb3.to_checksum_address(pair['pair_address'])
                    
                    pair_contract = self.w3.eth.contract(
                        address=pair_addr, 
                        abi=[{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"internalType":"uint112","name":"_reserve0","type":"uint112"},{"internalType":"uint112","name":"_reserve1","type":"uint112"},{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],"payable":False,"stateMutability":"view","type":"function"},
                             {"constant":True,"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}]
                    )
                    
                    reserves = await pair_contract.functions.getReserves().call()
                    token0 = await pair_contract.functions.token0().call()
                    r0, r1, _ = reserves
                    
                    # WBNB Address
                    wbnb_addr = AsyncWeb3.to_checksum_address("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
                    is_token0_wbnb = (token0 == wbnb_addr)
                    
                    bnb_reserve = r0 if is_token0_wbnb else r1
                    token_reserve = r1 if is_token0_wbnb else r0
                    
                    # 1. Check for Rug (Zero Liquidity)
                    # If BNB reserve < 0.1 BNB, consider it dead/rugged
                    is_dead = (bnb_reserve < 1e17) 
                    
                    status = "unknown"
                    
                    if is_dead:
                        status = "correct_reject" # Correctly rejected a rug
                    else:
                        # 2. Check Price Performance (Missed Opportunity?)
                        # Current Price = BNB / Token
                        current_price = 0
                        if token_reserve > 0:
                            current_price = bnb_reserve / token_reserve
                            
                        initial_price = pair['price_at_discovery']
                        
                        if initial_price and initial_price > 0:
                            roi = (current_price - initial_price) / initial_price
                            
                            if roi > 0.5: # If price increased by >50%, we missed a good one
                                status = "false_positive" # Missed opportunity
                            elif roi < -0.5: # If price dropped >50%, correct reject
                                status = "correct_reject"
                            else:
                                status = "neutral" # Stagnant
                        else:
                            # No initial price, just assume neutral unless dead
                            status = "neutral"
                    
                    await db.execute(
                        "UPDATE pairs SET verification_status = ? WHERE pair_address = ?",
                        (status, pair['pair_address'])
                    )
                    
                except Exception as e:
                    logger.error(f"Error verifying pair {pair['pair_address']}: {e}")
                    
            await db.commit()

    async def get_bnb_price_usd(self):
        """Fetch current BNB price in USD"""
        try:
            async with aiohttp.ClientSession() as session:
                # Try Binance first
                try:
                    async with session.get("https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT", timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return float(data['price'])
                except Exception:
                    pass
                
                # Try CoinGecko fallback
                try:
                    async with session.get("https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd", timeout=5) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            return float(data['binancecoin']['usd'])
                except Exception:
                    pass
                    
            return 0.0
        except Exception as e:
            logger.warning(f"Failed to fetch BNB price: {e}")
            return 0.0

    async def get_trend_text(self, target_date):
        """Analyze trend based on last 7 days PnL"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                # Get last 7 days including target date
                cursor = await db.execute(
                    "SELECT date, net_pnl_bnb FROM daily_stats WHERE date <= ? ORDER BY date DESC LIMIT 7", 
                    (target_date,)
                )
                rows = await cursor.fetchall()
                
            if len(rows) < 2:
                return "➡️ 数据不足"
                
            pnls = [r['net_pnl_bnb'] for r in rows][::-1] # Reverse to chronological
            
            # Compare avg of second half vs first half
            mid = len(pnls) // 2
            first_half = pnls[:mid]
            second_half = pnls[mid:]
            
            avg1 = sum(first_half) / len(first_half)
            avg2 = sum(second_half) / len(second_half)
            
            diff = avg2 - avg1
            
            if diff > 0.05:
                return "📈 持续改善"
            elif diff < -0.05:
                return "📉 表现下滑"
            else:
                return "➡️ 保持平稳"
        except Exception:
            return "➡️ 计算中"

    async def generate_daily_report_text(self, target_date=None):
        if not target_date:
            target_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
        
        # Ensure stats are calculated
        await self.calculate_daily_stats(target_date)
        
        # Fetch BNB Price
        bnb_price = await self.get_bnb_price_usd()
        
        # Get Trend
        trend_text = await self.get_trend_text(target_date)
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM daily_stats WHERE date = ?", (target_date,)) as cursor:
                row = await cursor.fetchone()
                
            if not row:
                return f"No data for {target_date}"
                
            stats = dict(row)
            
            # Format Report
            # Use .get() for safety
            win_rate_pct = int(stats.get('win_rate', 0) * 100)
            
            best_token = stats.get('best_token') or 'N/A'
            best_pnl = stats.get('best_pnl_pct', 0)
            if best_pnl == -999: best_pnl = 0
            
            worst_token = stats.get('worst_token') or 'N/A'
            worst_pnl = stats.get('worst_pnl_pct', 0)
            if worst_pnl == 999: worst_pnl = 0
            
            net_pnl_bnb = stats.get('net_pnl_bnb', 0)
            net_pnl_str = f"{net_pnl_bnb:+.4f} BNB"
            if bnb_price > 0:
                net_pnl_usd = net_pnl_bnb * bnb_price
                net_pnl_str += f" (${net_pnl_usd:+.2f})"
            
            report = f"""📊 Daily Report ({target_date})
━━━━━━━━━━━━━━━
New Coins Found: {stats.get('new_coins_found', 0)}
Passed Screening: {stats.get('passed_screening', 0)}
Actual Bought: {stats.get('actual_bought', 0)}
Filter Accuracy:
- Correct Rejects (Rug): {stats.get('correct_rejected', 0)}
- Missed Opportunities: {stats.get('false_rejected', 0)}
━━━━━━━━━━━━━━━
Trades: {stats.get('total_trades', 0)} (Wins: {stats.get('win_count', 0)} | Losses: {stats.get('loss_count', 0)})
Win Rate: {win_rate_pct}%
Net PnL: {net_pnl_str}
Max Drawdown: {stats.get('max_drawdown', 0):.4f} BNB
━━━━━━━━━━━━━━━
Best: {best_token} ({best_pnl:+.1f}%)
Worst: {worst_token} ({worst_pnl:+.1f}%)
━━━━━━━━━━━━━━━
Strategy EV: {stats.get('expected_value', 0):.4f} BNB/Trade
本周趋势: {trend_text}
"""
            return report

    async def send_telegram_report(self, report_text: str):
        """Send report via Telegram"""
        # Try to get from env, or config
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not token or not chat_id:
            logger.warning("Telegram config missing, skipping report sending")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": report_text,
            # "parse_mode": "HTML" 
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"Telegram report failed: {await response.text()}")
                    else:
                        logger.info("Daily report sent to Telegram")
        except Exception as e:
            logger.error(f"Telegram error: {e}")

    async def generate_weekly_report(self):
        """Generate weekly report with sensitivity analysis"""
        today = datetime.now()
        start_date = (today - timedelta(days=7)).strftime("%Y-%m-%d")
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # 1. Basic Stats
            async with db.execute(
                "SELECT * FROM daily_stats WHERE date >= ? ORDER BY date ASC", 
                (start_date,)
            ) as cursor:
                rows = await cursor.fetchall()
                
            if not rows:
                return "本周无数据"
                
            total_trades = sum(r['total_trades'] for r in rows)
            total_win = sum(r['win_count'] for r in rows)
            total_pnl = sum(r['net_pnl_bnb'] for r in rows)
            win_rate = (total_win / total_trades * 100) if total_trades > 0 else 0
            
            report = f"""📊 Weekly Report (Last 7 Days)
━━━━━━━━━━━━━━━
Total Trades: {total_trades}
Win Rate: {win_rate:.1f}%
Net PnL: {total_pnl:.4f} BNB
━━━━━━━━━━━━━━━
"""

            # 2. Sensitivity Analysis (Join Positions & Pairs)
            query = """
                SELECT 
                    p.dex_name, 
                    p.security_score,
                    p.discovered_at,
                    pos.buy_time,
                    pos.sold_portions
                FROM positions pos
                LEFT JOIN pairs p ON pos.token_address = p.target_token
                WHERE pos.sold_portions IS NOT NULL AND pos.sold_portions != '[]'
            """
            
            async with db.execute(query) as cursor:
                rows = await cursor.fetchall()
                
            # Process in Python
            dex_stats = {} # dex -> {wins, total, pnl}
            score_stats = {} # range -> {wins, total}
            wait_time_stats = {} # bucket -> {wins, total, pnl}
            loss_dist_stats = {} # bucket -> count
            
            for row in rows:
                try:
                    sold_data = json.loads(row['sold_portions'])
                    
                    total_pnl = sum(float(x.get('pnl', 0)) for x in sold_data)
                    is_win = total_pnl > 0
                    
                    # DEX Stats
                    dex = row['dex_name'] or 'Unknown'
                    if dex not in dex_stats: dex_stats[dex] = {'wins':0, 'total':0, 'pnl':0.0}
                    dex_stats[dex]['total'] += 1
                    dex_stats[dex]['pnl'] += total_pnl
                    if is_win: dex_stats[dex]['wins'] += 1
                    
                    # Score Stats
                    score = row['security_score'] or 0
                    score_range = f"{score//10*10}-{score//10*10+10}" # e.g. 80-90
                    if score_range not in score_stats: score_stats[score_range] = {'wins':0, 'total':0}
                    score_stats[score_range]['total'] += 1
                    if is_win: score_stats[score_range]['wins'] += 1

                    # Wait Time Analysis
                    buy_time = row['buy_time']
                    discovered_at_str = row['discovered_at']
                    if buy_time and discovered_at_str:
                        try:
                            # Handle potential formats
                            if "T" in discovered_at_str:
                                discovered_dt = datetime.fromisoformat(discovered_at_str)
                            else:
                                # "YYYY-MM-DD HH:MM:SS.mmmmmm"
                                discovered_dt = datetime.strptime(discovered_at_str.split('.')[0], "%Y-%m-%d %H:%M:%S")
                            
                            discovery_ts = discovered_dt.timestamp()
                            wait_seconds = buy_time - discovery_ts
                            
                            if wait_seconds < 10: bucket = "<10s"
                            elif wait_seconds < 30: bucket = "10-30s"
                            elif wait_seconds < 60: bucket = "30-60s"
                            else: bucket = ">60s"
                            
                            if bucket not in wait_time_stats: wait_time_stats[bucket] = {'wins':0, 'total':0, 'pnl':0.0}
                            wait_time_stats[bucket]['total'] += 1
                            wait_time_stats[bucket]['pnl'] += total_pnl
                            if is_win: wait_time_stats[bucket]['wins'] += 1
                        except Exception as e:
                            # logger.warning(f"Date parsing error: {e}")
                            pass

                    # Loss Distribution (Proxy for Stop Loss Sensitivity)
                    if not is_win:
                        # Calculate total PnL % for the position
                        # We need cost basis. If not available, use buy_amount or approx.
                        # Assuming sold_data has 'bnb_got' and 'pnl'
                        bnb_got = sum(float(x.get('bnb_got', 0)) for x in sold_data)
                        cost = bnb_got - total_pnl
                        if cost > 0:
                            loss_pct = abs(total_pnl / cost) * 100
                            if loss_pct < 10: l_bucket = "0-10%"
                            elif loss_pct < 20: l_bucket = "10-20%"
                            elif loss_pct < 50: l_bucket = "20-50%"
                            else: l_bucket = ">50%"
                            
                            loss_dist_stats[l_bucket] = loss_dist_stats.get(l_bucket, 0) + 1

                except Exception as e:
                    continue
            
            # Format Analysis
            analysis_text = "\n🔍 参数敏感性分析 (All Time)\n"
            
            # DEX
            analysis_text += "【DEX 表现】\n"
            for dex, d in dex_stats.items():
                wr = (d['wins']/d['total']*100) if d['total']>0 else 0
                analysis_text += f"- {dex}: WR {wr:.0f}% ({d['wins']}/{d['total']}), PnL {d['pnl']:.2f}\n"
                
            # Score
            analysis_text += "\n【安全分与胜率】\n"
            for rng in sorted(score_stats.keys(), reverse=True):
                d = score_stats[rng]
                wr = (d['wins']/d['total']*100) if d['total']>0 else 0
                analysis_text += f"- 分数 {rng}: WR {wr:.0f}% ({d['wins']}/{d['total']})\n"

            # Wait Time
            analysis_text += "\n【买入等待时间】\n"
            # Sort buckets order
            bucket_order = ["<10s", "10-30s", "30-60s", ">60s"]
            for b in bucket_order:
                if b in wait_time_stats:
                    d = wait_time_stats[b]
                    wr = (d['wins']/d['total']*100) if d['total']>0 else 0
                    analysis_text += f"- {b}: WR {wr:.0f}% ({d['wins']}/{d['total']}), PnL {d['pnl']:.2f}\n"

            # Loss Distribution
            analysis_text += "\n【亏损分布 (止损参考)】\n"
            loss_order = ["0-10%", "10-20%", "20-50%", ">50%"]
            for b in loss_order:
                count = loss_dist_stats.get(b, 0)
                if count > 0:
                    analysis_text += f"- {b}: {count} 单\n"
                
            return report + analysis_text

# Example Usage
if __name__ == "__main__":
    async def main():
        # Setup logging
        logging.basicConfig(level=logging.INFO)
        
        analyzer = PerformanceAnalyzer()
        await analyzer.init_db()
        
        # Test Daily Stats
        stats = await analyzer.calculate_daily_stats() # Yesterday
        print("Stats:", stats)
        
        # Test Report Text
        report = await analyzer.generate_daily_report_text()
        print("\nReport Preview:\n", report)
        
        # Test Weekly Report
        weekly = await analyzer.generate_weekly_report()
        print("\nWeekly Preview:\n", weekly)
        
    asyncio.run(main())
