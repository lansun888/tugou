import asyncio
import aiosqlite
import logging
import json
import time
from datetime import datetime, timedelta
from typing import Dict, List, Any

logger = logging.getLogger("SimulationManager")

class SimulationManager:
    """
    Simulation Manager for backtesting and forward testing (simulation mode).
    Handles statistics calculation, reporting, and automatic switching suggestions.
    """
    INITIAL_BALANCE_BNB = 1.0  # Starting balance for all simulations

    def __init__(self, db_path="data/bsc_bot.db"):
        self.db_path = db_path
        self.trades_table = "simulation_trades"
        self.positions_table = "simulation_positions"
        self.balance_bnb = self.INITIAL_BALANCE_BNB

    async def recalculate_balance(self):
        """从 DB 中的仓位数据动态计算当前模拟余额。
        公式: 初始余额 - 所有买入花费 + 所有卖出收入"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    f"SELECT buy_amount_bnb, sold_portions FROM {self.positions_table}"
                ) as cursor:
                    rows = await cursor.fetchall()

            total_spent = 0.0
            total_received = 0.0

            for row in rows:
                total_spent += float(row["buy_amount_bnb"] or 0)
                portions_raw = row["sold_portions"]
                if portions_raw and portions_raw != "[]":
                    try:
                        for portion in json.loads(portions_raw):
                            total_received += float(portion.get("bnb_got", 0))
                    except Exception:
                        pass

            self.balance_bnb = round(
                self.INITIAL_BALANCE_BNB - total_spent + total_received, 6
            )
        except Exception as e:
            logger.error(f"recalculate_balance failed: {e}")

    async def init_db(self):
        """Initialize simulation tables if they don't exist"""
        async with aiosqlite.connect(self.db_path) as db:
            # Simulation Trades Table (same structure as trades but for simulation)
            await db.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.trades_table} (
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
            # Migration: add missing columns if table already existed with old schema
            cursor = await db.execute(f"PRAGMA table_info({self.trades_table})")
            cols = [row[1] for row in await cursor.fetchall()]
            migrations = [
                ("amount_token", "TEXT"), ("amount_bnb", "TEXT"), ("price_bnb", "TEXT"),
                ("token_name", "TEXT"), ("note", "TEXT"),
                ("pnl_bnb", "REAL DEFAULT 0"), ("pnl_percentage", "REAL DEFAULT 0"),
            ]
            for col_name, col_type in migrations:
                if col_name not in cols:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN {col_name} {col_type}")
            
            # Simulation Positions Table is handled by PositionManager, but we can ensure it exists here too
            # (PositionManager will create it if we configure it correctly)
            await db.commit()

    async def get_simulation_stats(self, days=7) -> Dict[str, Any]:
        """Get simulation statistics for the last N days"""
        return await self._calculate_stats_from_positions(days)

    async def get_first_trade_time(self):
        """Get the timestamp of the first trade"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                # Check trades table for earliest timestamp
                async with db.execute(f"SELECT MIN(created_at) FROM {self.trades_table}") as cursor:
                    row = await cursor.fetchone()
                    if row and row[0]:
                        # timestamp format in trades table: CURRENT_TIMESTAMP (YYYY-MM-DD HH:MM:SS)
                        try:
                            return datetime.strptime(row[0], "%Y-%m-%d %H:%M:%S")
                        except ValueError:
                            # Try ISO format if different
                            return datetime.fromisoformat(row[0])
            return None
        except Exception as e:
            logger.error(f"Error getting first trade time: {e}")
            return None

    async def _calculate_stats_from_positions(self, days=7):
        """Calculate stats from positions table (sold_portions)"""
        try:
            start_timestamp = (datetime.now() - timedelta(days=days)).timestamp()
            
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                # Fetch all positions (active and closed) that have sold_portions
                # We need both active (partial sells) and closed
                query = f"SELECT sold_portions FROM {self.positions_table} WHERE sold_portions IS NOT NULL AND sold_portions != '[]'"
                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()
            
            total_trades = 0
            win_count = 0
            loss_count = 0
            total_profit = 0.0
            pnl_list = []
            
            for row in rows:
                try:
                    sold_portions = json.loads(row['sold_portions'])
                    for sell in sold_portions:
                        # check time
                        if sell.get('time', 0) >= start_timestamp:
                            pnl = float(sell.get('pnl', 0))
                            total_profit += pnl
                            pnl_list.append(pnl)
                            if pnl > 0:
                                win_count += 1
                            else:
                                loss_count += 1
                            total_trades += 1
                except:
                    continue
            
            win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0
            avg_profit = sum([p for p in pnl_list if p > 0]) / win_count if win_count > 0 else 0
            avg_loss = sum([p for p in pnl_list if p <= 0]) / loss_count if loss_count > 0 else 0
            
            loss_rate = 1 - (win_rate / 100)
            expected_value = ((win_rate / 100) * avg_profit) + (loss_rate * avg_loss)
            
            return {
                "total_trades": total_trades,
                "win_count": win_count,
                "loss_count": loss_count,
                "win_rate": round(win_rate, 2),
                "total_profit_bnb": round(total_profit, 4),
                "avg_profit_bnb": round(avg_profit, 4),
                "avg_loss_bnb": round(avg_loss, 4),
                "expected_value": round(expected_value, 4),
                "days": days
            }
        except Exception as e:
            logger.error(f"Error calculating from positions: {e}")
            return {
                "total_trades": 0,
                "win_rate": 0.0,
                "profit_bnb": 0.0,
                "expected_value": 0.0,
                "days": days,
                "error": str(e)
            }

    async def analyze_switching_criteria(self) -> Dict[str, Any]:
        """
        Analyze if simulation results justify switching to live trading.
        Criteria:
        - > 7 days of data (or at least significant number of trades)
        - Win rate > 35% -> Suggest Switch
        - Win rate < 20% -> Suggest Optimization
        - EV < 0 -> Warning
        """
        stats = await self.get_simulation_stats(days=7)
        first_trade_time = await self.get_first_trade_time()
        
        days_running = 0
        if first_trade_time:
            days_running = (datetime.now() - first_trade_time).days
            
        # Criteria for "Enough Data":
        # 1. Running for at least 7 days AND at least 10 trades
        # 2. OR running for less than 7 days but has significant volume (> 50 trades)
        is_mature = (days_running >= 7 and stats["total_trades"] >= 10) or (stats["total_trades"] >= 50)
        
        if not stats or not is_mature:
            return {
                "action": "wait",
                "message": f"Insufficient data (Running: {days_running} days, Trades: {stats.get('total_trades', 0)})",
                "stats": stats,
                "days_running": days_running
            }
            
        win_rate = stats["win_rate"]
        ev = stats["expected_value"]
        
        result = {
            "action": "wait",
            "message": "Continue simulation",
            "stats": stats,
            "suggestions": [],
            "days_running": days_running
        }
        
        if win_rate > 35 and ev > 0:
            result["action"] = "switch_to_live"
            result["message"] = "🚀 Simulation successful! Win rate > 35% and positive EV. Recommended to switch to Live Mode."
        elif win_rate < 20:
            result["action"] = "optimize"
            result["message"] = "⚠️ Low win rate (< 20%). Parameter optimization required."
            result["suggestions"] = [
                "Increase 'min_security_score' in config",
                "Adjust 'stop_loss_percentage' (tighten or loosen)",
                "Review 'check_honeypot' settings"
            ]
        elif ev < 0:
            result["action"] = "warning"
            result["message"] = "⚠️ Negative Expected Value. Strategy is losing money in simulation."
            
        return result

    async def send_telegram_alert(self, message: str):
        """Send alert via Telegram"""
        import os
        import aiohttp
        
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")
        
        if not token or not chat_id:
            # logger.warning("Telegram config missing, skipping simulation alert")
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {
            "chat_id": chat_id,
            "text": message,
            "parse_mode": "Markdown"
        }
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"Failed to send Telegram alert: {await response.text()}")
                    else:
                        logger.info("Simulation alert sent via Telegram")
        except Exception as e:
            logger.error(f"Error sending Telegram alert: {e}")

    async def check_and_send_alerts(self):
        """Check simulation criteria and send alerts if needed"""
        logger.info("Checking simulation switching criteria...")
        
        analysis = await self.analyze_switching_criteria()
        
        # If action is meaningful (not wait), send alert
        if analysis["action"] in ["switch_to_live", "optimize", "warning"]:
            report = await self.generate_report(analysis)
            await self.send_telegram_alert(report)
            return True
            
        return False

    async def generate_report(self, analysis=None):
        """Generate a text report for Telegram/Logs"""
        if analysis is None:
            analysis = await self.analyze_switching_criteria()
            
        stats = analysis["stats"]
        days = analysis.get("days_running", 0)
        
        report = f"""
🤖 **Simulation Report (Running {days} Days)**
━━━━━━━━━━━━━━━━━━━━
📊 **Stats**:
  • Trades: {stats.get('total_trades', 0)}
  • Win Rate: {stats.get('win_rate', 0)}%
  • Net Profit: {stats.get('total_profit_bnb', 0)} BNB
  • Exp. Value: {stats.get('expected_value', 0)} BNB/trade

💡 **Recommendation**:
  {analysis['message']}
"""
        if analysis.get("suggestions"):
            report += "\n🛠 **Suggestions**:\n"
            for s in analysis["suggestions"]:
                report += f"  - {s}\n"
                
        return report

# For manual running
if __name__ == "__main__":
    async def main():
        sim = SimulationManager()
        await sim.init_db()
        print(await sim.generate_report())
        
    asyncio.run(main())
