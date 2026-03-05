import aiohttp
import asyncio
import time
import json
import logging
from typing import Dict, List, Optional, Tuple
from dataclasses import dataclass, field, asdict
from decimal import Decimal
import aiosqlite
from loguru import logger
from datetime import datetime, timedelta
import os
from dotenv import load_dotenv
from web3 import AsyncWeb3
from bsc_bot.monitor.abis import PANCAKESWAP_PAIR_ABI, ERC20_ABI, PANCAKESWAP_V2_FACTORY_ABI

load_dotenv()

@dataclass
class Position:
    token_address: str
    token_name: str
    buy_price_bnb: float
    buy_amount_bnb: float
    token_amount: float
    buy_time: float  # timestamp
    current_price: float = 0.0
    current_value_bnb: float = 0.0
    pnl_percentage: float = 0.0
    highest_price: float = 0.0
    sold_portions: List[Dict] = field(default_factory=list)
    status: str = "active" # active, closed
    buy_gas_price: int = 0  # Gas Price at buy time (wei)
    fetch_fail_count: int = 0 # Consecutive price fetch failures
    last_update_time: float = 0.0 # Timestamp of last price update
    
    # DexScreener Data
    volume_24h: float = 0.0
    price_change_5m: float = 0.0
    market_cap: float = 0.0
    txns_5m_buys: int = 0
    txns_5m_sells: int = 0
    source: str = "init"
    
    def update_price(self, new_price: float):
        self.current_price = new_price
        self.last_update_time = time.time()
        
        # Apply slippage/tax deduction for realistic unrealized PnL (e.g., 15%)
        # This makes the "current value" represent what you'd actually get if you sold now
        deduction_rate = 0.15 
        effective_price = new_price * (1 - deduction_rate)
        
        self.current_value_bnb = self.token_amount * effective_price
        if self.buy_price_bnb > 0:
            # PnL should be based on (Effective Sell Value - Cost) / Cost
            self.pnl_percentage = ((effective_price - self.buy_price_bnb) / self.buy_price_bnb) * 100
        else:
            self.pnl_percentage = 0.0
            
        if new_price > self.highest_price:
            self.highest_price = new_price

class PositionManager:
    def __init__(self, executor, db_path="bsc_bot.db", mode=None):
        self.executor = executor
        self.db_path = db_path
        self.mode = mode or "live"
        self.positions_table = "simulation_positions" if self.mode == "simulation" else "positions"

        self.positions: Dict[str, Position] = {} # token_address -> Position
        self.config = executor.config.get("position_management", {})
        self.running = False
        self._tg_session: aiohttp.ClientSession = None  # 复用Telegram通知session
        self._pair_address_cache: Dict[str, str] = {}  # token_address -> pair_address 缓存
        
        # Lock for duplicate sell prevention (in-memory)
        self.selling_tokens = set()
        
        # Track pending stop losses for "N consecutive confirmations"
        # Format: {token_address: {"count": int, "first_trigger_time": float}}
        self.pending_stop_loss = {}
        
        # 每日统计
        self.daily_stats = {
            "date": datetime.now().strftime("%Y-%m-%d"),
            "buy_count": 0,
            "sell_count": 0,
            "win_count": 0,
            "loss_count": 0,
            "profit_bnb": 0.0,
            "loss_bnb": 0.0
        }

    async def init_manager(self):
        """初始化：创建表并恢复持仓"""
        await self._init_db()
        await self._load_positions()
        logger.info("仓位管理器初始化完成")

    async def _init_db(self):
        """创建仓位表"""
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(f"""
                CREATE TABLE IF NOT EXISTS {self.positions_table} (
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
                    current_price REAL DEFAULT 0.0,
                    pnl_percentage REAL DEFAULT 0.0,
                    volume_24h REAL DEFAULT 0.0,
                    price_change_5m REAL DEFAULT 0.0,
                    market_cap REAL DEFAULT 0.0,
                    txns_5m_buys INTEGER DEFAULT 0,
                    txns_5m_sells INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'init'
                )
            """)
            
            # Migration check
            try:
                cursor = await db.execute(f"PRAGMA table_info({self.positions_table})")
                columns = [row[1] for row in await cursor.fetchall()]
                
                if "volume_24h" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN volume_24h REAL DEFAULT 0.0")
                if "price_change_5m" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN price_change_5m REAL DEFAULT 0.0")
                if "market_cap" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN market_cap REAL DEFAULT 0.0")
                if "txns_5m_buys" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN txns_5m_buys INTEGER DEFAULT 0")
                if "txns_5m_sells" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN txns_5m_sells INTEGER DEFAULT 0")
                if "source" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN source TEXT DEFAULT 'init'")
            except Exception as e:
                logger.warning(f"Migration check failed, running legacy migration: {e}")
                try:
                    async with db.execute(f"PRAGMA table_info({self.positions_table})") as cursor:
                        columns = [row[1] for row in await cursor.fetchall()]
                    if "buy_gas_price" not in columns:
                        logger.info(f"Migrating {self.positions_table} table: adding buy_gas_price")
                        await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN buy_gas_price INTEGER DEFAULT 0")
                    if "current_price" not in columns:
                        logger.info(f"Migrating {self.positions_table} table: adding current_price")
                        await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN current_price REAL DEFAULT 0.0")
                    if "pnl_percentage" not in columns:
                        logger.info(f"Migrating {self.positions_table} table: adding pnl_percentage")
                        await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN pnl_percentage REAL DEFAULT 0.0")
                except Exception as e2:
                    logger.warning(f"Legacy migration also failed: {e2}")
                
            await db.commit()

    async def _load_positions(self):
        """从数据库恢复活跃仓位"""
        try:
            logger.info(f"Loading positions from {self.db_path} table {self.positions_table}")
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
                # Debug: check all rows
                async with db.execute(f"SELECT * FROM {self.positions_table}") as cursor:
                    all_rows = await cursor.fetchall()
                    logger.info(f"Total rows in {self.positions_table}: {len(all_rows)}")
                    for r in all_rows:
                        logger.info(f"Row: {dict(r)}")

                # Load both active and partially_sold positions
                query = f"SELECT * FROM {self.positions_table} WHERE status IN ('active', 'partially_sold')"
                async with db.execute(query) as cursor:
                    rows = await cursor.fetchall()
                    for row in rows:
                        row_dict = dict(row)
                        pos = Position(
                            token_address=row_dict['token_address'],
                            token_name=row_dict['token_name'],
                            buy_price_bnb=row_dict['buy_price_bnb'],
                            buy_amount_bnb=row_dict['buy_amount_bnb'],
                            token_amount=row_dict['token_amount'],
                            buy_time=row_dict['buy_time'],
                            highest_price=row_dict['highest_price'],
                            sold_portions=json.loads(row_dict['sold_portions']),
                            status=row_dict['status'],
                            buy_gas_price=row_dict.get('buy_gas_price', 0) or 0,
                            current_price=row_dict.get('current_price', 0.0) or 0.0,
                            pnl_percentage=row_dict.get('pnl_percentage', 0.0) or 0.0
                        )
                        # Recalculate values if needed
                        pos.current_value_bnb = pos.token_amount * pos.current_price
                        self.positions[pos.token_address] = pos
            logger.info(f"恢复了 {len(self.positions)} 个活跃仓位")
        except Exception as e:
            logger.error(f"恢复仓位失败: {e}")

    async def add_position(self, token_address, token_name, buy_price, buy_amount_bnb, token_amount, buy_gas_price=0, dex_data=None):
        """添加新仓位"""
        # 每日风控检查
        if not self._check_daily_risk_allow_buy():
            logger.warning("触发每日风控，停止买入")
            return False

        pos = Position(
            token_address=token_address,
            token_name=token_name,
            buy_price_bnb=buy_price,
            buy_amount_bnb=buy_amount_bnb,
            token_amount=token_amount,
            buy_time=time.time(),
            highest_price=buy_price,
            sold_portions=[],
            status="active",
            buy_gas_price=buy_gas_price
        )
        
        if dex_data:
            pos.volume_24h = dex_data.get('volume_24h', 0.0)
            pos.price_change_5m = dex_data.get('price_change_5m', 0.0)
            pos.market_cap = dex_data.get('market_cap', 0.0)
            pos.txns_5m_buys = dex_data.get('txns_5m_buys', 0)
            pos.txns_5m_sells = dex_data.get('txns_5m_sells', 0)
            pos.source = "dexscreener"
            
        pos.update_price(buy_price) # 初始化当前价格
        
        self.positions[token_address] = pos
        await self._save_position(pos)
        
        # 更新每日统计
        self._update_daily_stats(buy=True)
        return True

    async def _save_position(self, pos: Position):
        """保存仓位到数据库"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(f"""
                    INSERT OR REPLACE INTO {self.positions_table} 
                    (token_address, token_name, buy_price_bnb, buy_amount_bnb, token_amount, buy_time, highest_price, sold_portions, status, buy_gas_price, current_price, pnl_percentage)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pos.token_address, pos.token_name, pos.buy_price_bnb, pos.buy_amount_bnb, 
                    pos.token_amount, pos.buy_time, pos.highest_price, json.dumps(pos.sold_portions), pos.status, pos.buy_gas_price,
                    pos.current_price, pos.pnl_percentage
                ))
                await db.commit()
        except Exception as e:
            logger.error(f"保存仓位失败: {e}")

    async def start_monitoring(self):
        """启动监控循环 (Batch Mode + On-Chain Fallback)"""
        self.running = True
        logger.info("启动仓位监控循环 (Batch Mode + On-Chain Fallback)...")
        
        from utils.dexscreener_client import get_batch_prices
        
        last_dashboard_time = 0
        monitor_interval = self.config.get("monitor_interval", 5) # Default to 5s for batch updates
        
        while self.running:
            try:
                # 1. 每日统计重置
                self._check_daily_reset()
                
                # 2. Get active tokens
                active_tokens = list(self.positions.keys())
                if not active_tokens:
                    await asyncio.sleep(monitor_interval)
                    continue
                
                # 3. Batch Query (DexScreener)
                all_prices = {}
                try:
                    all_prices = await get_batch_prices(active_tokens)
                except Exception as e:
                    logger.warning(f"Batch price fetch failed (Network Issue?): {e}")
                    # Proceed to individual fallback
                
                # 4. Process each position in PARALLEL
                tasks = [self._process_single_token(addr, all_prices) for addr in active_tokens]
                results = await asyncio.gather(*tasks, return_exceptions=True)
                for addr, exc in zip(active_tokens, results):
                    if isinstance(exc, Exception):
                        logger.error(f"并行处理 {addr} 异常: {exc}")

                # Dashboard log every 30s
                if time.time() - last_dashboard_time > 30:
                    self._log_dashboard()
                    last_dashboard_time = time.time()

                await asyncio.sleep(monitor_interval)

            except Exception as e:
                logger.error(f"监控循环异常: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def _process_single_token(self, token_addr: str, all_prices: dict):
        """处理单个代币的价格更新和策略检查（并行安全版）"""
        pos = self.positions.get(token_addr)
        if not pos or pos.status in ("sold", "closed"):
            return

        pair_data = all_prices.get(token_addr.lower()) if all_prices else None

        price_bnb = 0.0
        liquidity_bnb = 0.0
        source = "failed"

        if pair_data:
            price_bnb = pair_data.get('price_bnb', 0.0)
            liquidity_bnb = pair_data.get('liquidity_bnb', 0.0)
            pos.volume_24h = pair_data.get('volume_24h', 0.0)
            pos.price_change_5m = pair_data.get('price_change_5m', 0.0)
            pos.market_cap = pair_data.get('market_cap', 0.0)
            pos.txns_5m_buys = pair_data.get('txns_5m_buys', 0)
            pos.txns_5m_sells = pair_data.get('txns_5m_sells', 0)
            pos.source = "dexscreener"
            source = "dexscreener"
        else:
            try:
                price_info, err = await self._get_price_onchain(token_addr)
                if price_info:
                    price_bnb = price_info.get('price_bnb', 0.0)
                    liquidity_bnb = price_info.get('liquidity_bnb', 0.0)
                    source = "on_chain"
                elif err == "network":
                    logger.warning(f"{pos.token_name}: 链上查询失败(网络)，跳过")
                    pos.fetch_fail_count += 1
                    return
                elif err == "onchain_empty":
                    price_bnb = 0.0
                    liquidity_bnb = 0.0
                    source = "on_chain_empty"
            except Exception as e:
                logger.warning(f"链上价格回退异常 {token_addr}: {e}")
                pos.fetch_fail_count += 1
                return

        pos.fetch_fail_count = 0
        await self._update_position_price(pos, price_bnb, source, liquidity_bnb)
        await self._check_strategies(pos, price_bnb, liquidity_bnb)

    async def _update_position_price(self, pos, price_bnb, source, liquidity_bnb):
        """Helper to update position price"""
        pos.update_price(price_bnb)
        pos.source = source
        # Persist to DB? User said "store in database". 
        # Maybe update DB periodically or on important events.
        # For performance, maybe not every tick. But let's leave it in memory for now, 
        # unless user explicitly asked to save *price history*. 
        # User said "Store in database for subsequent analysis" in Step 5 (New Coin Discovery).
        # For monitoring loop, usually memory is fine, but let's update current_price in DB occasionally if needed.
        pass

    async def _check_strategies(self, pos, current_price_bnb, liquidity_bnb):
        """Check TP/SL logic"""
        # Guard: skip already-sold positions (parallel tasks may race)
        if pos.status in ("sold", "closed"):
            return
        # 1. Rug Pull Check
        is_rug = False
        if current_price_bnb <= 0:
            is_rug = True
        elif liquidity_bnb > 0 and liquidity_bnb < 0.5: # Only if we have liquidity data
            is_rug = True
            
        if is_rug:
             logger.warning(f"⚠️ {pos.token_name} 疑似归零 (Price={current_price_bnb}, Liq={liquidity_bnb}), 等待5秒二次确认...")
             await asyncio.sleep(5)
             # Re-check single token
             data, err = await self._get_price_onchain(pos.token_address)
             
             if err == "network":
                 logger.warning("二次确认遇到网络错误，跳过止损")
                 return

             p2 = 0.0
             l2 = 0.0
             if data:
                 p2 = data.get('price_bnb', 0.0)
                 l2 = data.get('liquidity_bnb', 0.0)
             
             if err == "onchain_empty" or p2 <= 0 or (l2 > 0 and l2 < 0.5):
                 logger.error(f"💀 确认池子归零 (Rug Pull)! 执行清仓逻辑。")
                 pos.update_price(0.0)
                 await self._execute_sell(pos, 100, "rug_pull_confirmed")
                 return
             else:
                 logger.info(f"二次确认未归零，恢复正常 (Price={p2})")
                 current_price_bnb = p2
                 pos.update_price(p2) # Fix price

        # 2. Abnormal Pump Check
        if pos.current_price > 0 and current_price_bnb > pos.current_price * 10:
             logger.warning(f"⚠️ {pos.token_name} 价格异常暴涨 ({current_price_bnb/pos.current_price:.1f}倍)，疑似数据源错误")
             return

        # 3. Emergency Stop Loss
        if pos.highest_price > 0 and current_price_bnb > 0:
             drop_pct = (pos.highest_price - current_price_bnb) / pos.highest_price
             if drop_pct > 0.70:
                 logger.critical(f"⚠️ {pos.token_name} 暴跌 {drop_pct*100:.1f}%! 触发紧急止损!")
                 await self._execute_sell(pos, 100, "emergency_crash_stop")
                 return

        sold = False
        if not sold:
            sold = await self._check_take_profit(pos)
        if not sold:
            sold = await self._check_trailing_stop_loss(pos)
        if not sold:
            sold = await self._check_time_stop_loss(pos)
            
        if sold:
            if pos.token_amount <= 0 or pos.status in ("sold", "closed"):
                logger.info(f"仓位 {pos.token_name} 已关闭")
                if pos.token_address in self.positions:
                    del self.positions[pos.token_address]
            await self._save_position(pos)

    async def _get_price_onchain(self, token_address: str, pair_address=None) -> Tuple[Optional[dict], str]:
        """
        纯链上价格查询，不依赖任何外网API
        通过读取流动性池的储备量计算价格
        Returns: (price_data, error_type)
        error_type: "none", "network", "onchain_empty", "unknown"
        """
        try:
            w3 = self.executor.w3

            # 1. Get Pair Address（使用缓存避免重复查 factory）
            if not pair_address:
                cached = self._pair_address_cache.get(token_address)
                if cached:
                    pair_address = cached
                else:
                    factory_addr = await self.executor.router.functions.factory().call()
                    factory = w3.eth.contract(address=factory_addr, abi=PANCAKESWAP_V2_FACTORY_ABI)
                    pair_address = await factory.functions.getPair(
                        AsyncWeb3.to_checksum_address(token_address),
                        self.executor.WBNB_ADDRESS
                    ).call()
                    if pair_address != "0x0000000000000000000000000000000000000000":
                        self._pair_address_cache[token_address] = pair_address
            
            if pair_address == "0x0000000000000000000000000000000000000000":
                return None, "onchain_empty"
                
            # 2. Get Reserves
            pair_contract = w3.eth.contract(address=pair_address, abi=PANCAKESWAP_PAIR_ABI)
            reserves = await pair_contract.functions.getReserves().call()
            token0 = await pair_contract.functions.token0().call()
            
            reserve0, reserve1, _ = reserves
            WBNB = self.executor.WBNB_ADDRESS
            
            if token0.lower() == WBNB.lower():
                bnb_reserve = reserve0
                token_reserve = reserve1
            else:
                bnb_reserve = reserve1
                token_reserve = reserve0
                
            if token_reserve == 0:
                return None, "onchain_empty"
                
            # 3. Get Decimals
            token_contract = w3.eth.contract(address=AsyncWeb3.to_checksum_address(token_address), abi=ERC20_ABI)
            decimals = await token_contract.functions.decimals().call()
            
            # 4. Calculate Price
            price_bnb = (bnb_reserve / 10**18) / (token_reserve / 10**decimals)
            liquidity_bnb = (bnb_reserve / 10**18) * 2
            
            return {
                'price_bnb': price_bnb,
                'liquidity_bnb': liquidity_bnb,
                'source': 'onchain_reserves'
            }, "none"
            
        except Exception as e:
            logger.error(f"链上价格查询失败 {token_address}: {e}")
            return None, "network"



    async def _get_tg_session(self) -> aiohttp.ClientSession:
        """获取或创建 Telegram 通知用的持久化 session"""
        if self._tg_session is None or self._tg_session.closed:
            self._tg_session = aiohttp.ClientSession()
        return self._tg_session

    async def _send_telegram_notification(self, message: str):
        """发送Telegram通知（复用 session，减少连接开销）"""
        token = os.getenv("TELEGRAM_BOT_TOKEN")
        chat_id = os.getenv("TELEGRAM_CHAT_ID")

        if not token or not chat_id:
            return

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}

        try:
            session = await self._get_tg_session()
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as response:
                if response.status != 200:
                    logger.error(f"Telegram通知发送失败: {await response.text()}")
        except Exception as e:
            logger.error(f"Telegram通知发送异常: {e}")

    async def _check_trailing_stop_loss(self, pos: Position) -> bool:
        """策略二：追踪止损 (优化版：连续N次确认)"""
        cfg = self.config.get("trailing_stop", {})
        
        should_stop = False
        reason = ""
        
        # 1. 初始止损 (前30分钟)
        time_held = time.time() - pos.buy_time
        if time_held < 1800: # 30 mins
            initial_sl = cfg.get("initial_stop_loss", -50)
            if pos.pnl_percentage <= initial_sl:
                should_stop = True
                reason = "initial_stop_loss"
            
        # 2. 动态止损线
        if not should_stop and pos.buy_price_bnb > 0:
            # 计算当前倍数 (使用历史最高价)
            multiple = pos.highest_price / pos.buy_price_bnb
            
            # 找到当前倍数适用的最高止损线
            levels = cfg.get("levels", [])
            current_level_sl = -999
            
            for mult_threshold, sl_val in levels:
                if multiple >= mult_threshold:
                    current_level_sl = max(current_level_sl, sl_val)
                    
            if current_level_sl > -999:
                # 检查是否跌破动态止损线
                if pos.pnl_percentage <= current_level_sl:
                    should_stop = True
                    reason = f"trailing_stop_profit_{current_level_sl}"
            
        # 3. 回撤止损 (从最高点回撤)
        if not should_stop:
            pullback_limit = cfg.get("pullback_threshold", 40)
            if pos.highest_price > 0 and pos.highest_price > pos.buy_price_bnb * 1.1: # 至少涨一点
                pullback = (pos.highest_price - pos.current_price) / pos.highest_price * 100
                if pullback >= pullback_limit:
                     should_stop = True
                     reason = f"pullback_stop_{pullback_limit}"

        # 连续确认逻辑 (N=2, 间隔20秒)
        # 如果 should_stop 为 True，我们不立即卖出，而是开始/更新计数
        token = pos.token_address
        
        if should_stop:
            # 检查是否已有 Pending 记录
            if token not in self.pending_stop_loss:
                self.pending_stop_loss[token] = {
                    "count": 1, 
                    "first_trigger_time": time.time(),
                    "reason": reason
                }
                logger.info(f"{pos.token_name} 触发止损条件 ({reason})，等待二次确认...")
                return False # 第一次触发，不卖
            else:
                # 已有记录，检查时间间隔
                pending = self.pending_stop_loss[token]
                elapsed = time.time() - pending["first_trigger_time"]
                
                if elapsed > 15: # 超过15秒再次确认 (用户建议20秒，这里稍微放宽一点点确保能在下一次轮询抓到)
                    # 确认止损
                    logger.info(f"{pos.token_name} 二次确认止损 ({reason})，执行卖出!")
                    del self.pending_stop_loss[token] # 清除记录
                    await self._execute_sell(pos, 100, reason)
                    return True
                else:
                    # 时间未到，保持 Pending
                    return False
        else:
            # 如果价格恢复，清除 Pending 记录
            if token in self.pending_stop_loss:
                logger.info(f"{pos.token_name} 价格回升，取消止损警告")
                del self.pending_stop_loss[token]
            return False

    async def _execute_sell(self, pos: Position, percentage: float, reason: str):
        """Wrapper for sell execution with duplicate prevention"""
        # 1. In-memory Lock Check
        if pos.token_address in self.selling_tokens:
            # Special case: Rug Pull should bypass locks if possible, but concurrency might still be an issue.
            # However, if it's stuck in "selling" state for too long, we might need to force clear it?
            # For now, let's just log and skip. The lock is cleared in finally block.
            logger.warning(f"{pos.token_name} 正在卖出中，跳过并发触发 (Lock Active)")
            return False
            
        # 2. Database History Check (30s cool-down)
        # Skip this check if it's a Rug Pull (emergency)
        is_emergency = "rug" in reason.lower() or "emergency" in reason.lower()
        
        if is_emergency:
            logger.warning(f"🚨 {pos.token_name} 触发紧急卖出 ({reason})，跳过冷却检查")
        
        if not is_emergency:
            try:
                trades_table = self.executor.trades_table
                async with aiosqlite.connect(self.db_path) as db:
                    # Only check for SUCCESSFUL or PENDING sells in the last 30s. 
                    # If the last one FAILED, we should allow retrying immediately.
                    cursor = await db.execute(
                        f"SELECT COUNT(*) FROM {trades_table} WHERE token_address=? AND action='sell' AND status != 'failed' AND created_at > datetime('now', '-30 seconds')", 
                        (pos.token_address,)
                    )
                    count = (await cursor.fetchone())[0]
                    if count > 0:
                        logger.warning(f"{pos.token_name} 30秒内已有成功卖出 ({count}次)，跳过 (非紧急情况)")
                        return False
            except Exception as e:
                logger.error(f"防重复检查错误: {e}")

        self.selling_tokens.add(pos.token_address)
        try:
            return await self._execute_sell_impl(pos, percentage, reason)
        finally:
            self.selling_tokens.discard(pos.token_address)

    async def _execute_sell_impl(self, pos: Position, percentage: float, reason: str):
        """执行卖出逻辑 (优化版：动态滑点 + Gas竞争)"""
        # Guard: prevent double-sell from parallel tasks
        if pos.status in ("sold", "closed"):
            logger.info(f"{pos.token_name} 已售出，跳过重复卖出 (reason={reason})")
            return
        logger.info(f"触发卖出: {pos.token_name}, 原因: {reason}, 比例: {percentage}%")
        
        # 1. 获取流动性，决定滑点
        liquidity_bnb = await self.executor.get_pair_liquidity(pos.token_address)
        slippage = 15 # default
        
        if liquidity_bnb > 50:
            slippage = 12
        elif liquidity_bnb >= 10:
            slippage = 18
        else:
            slippage = 25 # Low liquidity, high slippage
            
        # Emergency Override
        if "rug" in reason.lower() or "emergency" in reason.lower():
             logger.warning("🚨 紧急模式：提升滑点至 49%")
             slippage = 49
            
        logger.info(f"当前流动性: {liquidity_bnb:.2f} BNB, 动态滑点: {slippage}%")
        
        # 2. 决定 Gas Price
        # max(current * 1.3, buy_gas * 1.1)
        current_gas_price = await self.executor._get_gas_price() # 调用 executor 的内部方法有点hacky，但为了方便
        buy_gas_price = pos.buy_gas_price or 0
        
        target_gas_price = max(int(current_gas_price * 1.3), int(buy_gas_price * 1.1))
        
        # 3. 预估 PnL（用于写入 trades 表）
        sell_amount_est = pos.token_amount * (percentage / 100)
        estimated_bnb = sell_amount_est * (pos.current_price or pos.buy_price_bnb)
        cost_of_sold = sell_amount_est * pos.buy_price_bnb
        est_pnl_bnb = estimated_bnb - cost_of_sold
        est_pnl_pct = (est_pnl_bnb / pos.buy_amount_bnb * 100) if pos.buy_amount_bnb > 0 else 0.0

        # 批量卖出逻辑 (如果卖出量 > 流动性 5%)
        sell_value_bnb = pos.current_value_bnb * (percentage / 100)

        if liquidity_bnb > 0 and sell_value_bnb > (liquidity_bnb * 0.05) and percentage > 99:
            logger.warning(f"卖出量 ({sell_value_bnb:.2f} BNB) 超过流动性 5%，启用分批卖出")
            res1 = await self.executor.sell_token(
                pos.token_address, pos.token_name, 50,
                slippage=slippage, gas_price=target_gas_price,
                simulated_balance=pos.token_amount,
                pnl_bnb=est_pnl_bnb * 0.5, pnl_percentage=est_pnl_pct,
                manual_price=pos.current_price
            )
            await asyncio.sleep(5)
            remaining_sim = pos.token_amount * 0.5
            res2 = await self.executor.sell_token(
                pos.token_address, pos.token_name, 100,
                slippage=slippage, gas_price=target_gas_price,
                simulated_balance=remaining_sim,
                pnl_bnb=est_pnl_bnb * 0.5, pnl_percentage=est_pnl_pct,
                manual_price=pos.current_price
            )
            res = res2
            total_bnb = float(res1.get("amount_bnb", 0)) + float(res2.get("amount_bnb", 0))
            res["amount_bnb"] = total_bnb
        else:
            # 正常卖出
            res = await self.executor.sell_token(
                pos.token_address, pos.token_name, percentage,
                slippage=slippage, gas_price=target_gas_price,
                simulated_balance=pos.token_amount,
                pnl_bnb=est_pnl_bnb, pnl_percentage=est_pnl_pct,
                manual_price=pos.current_price
            )
        
        if res["status"] == "success":
            bnb_got = float(res.get("amount_bnb", 0))
            
            # 更新仓位数据
            sell_amount = pos.token_amount * (percentage / 100)
            pos.token_amount -= sell_amount
            
            # Check if fully sold (or dust remaining)
            if pos.token_amount < 0.000001:
                pos.status = "sold"
                pos.token_amount = 0.0
                # Remove from in-memory dict so monitoring loop skips it
                self.positions.pop(pos.token_address, None)
            else:
                pos.status = "partially_sold"
            
            # 记录卖出历史
            sell_date = datetime.now().strftime("%Y-%m-%d")
            
            # 计算本次盈亏并更新每日统计
            # 估算成本: 总成本 * (卖出数量 / 初始数量)
            # 注意: 这里简化计算，假设 buy_amount_bnb 是总投入
            # 初始数量 = pos.token_amount (当前) + 已卖出... 比较复杂
            # 简单算法：本次收益 - (平均成本 * 卖出数量)
            cost_per_token = pos.buy_price_bnb
            cost_of_sold = sell_amount * cost_per_token
            pnl = bnb_got - cost_of_sold
            
            record = {
                "time": time.time(),
                "date": sell_date,
                "reason": reason,
                "percentage": percentage,
                "bnb_got": bnb_got,
                "price": pos.current_price,
                "amount": sell_amount,
                "pnl": pnl
            }
            pos.sold_portions.append(record)
            
            # Update Daily Stats (Sell)
            profit = pnl if pnl > 0 else 0.0
            loss = abs(pnl) if pnl < 0 else 0.0
            self._update_daily_stats(buy=False, profit=profit, loss=loss)
            
            # Save Position State Immediately
            await self._save_position(pos)
            
            # Notify
            profit_str = f"+{pnl:.4f}" if pnl > 0 else f"{pnl:.4f}"
            await self._send_telegram_notification(
                f"🔴 <b>卖出通知</b>\n"
                f"Token: {pos.token_name}\n"
                f"Reason: {reason}\n"
                f"PnL: {profit_str} BNB\n"
                f"Slippage: {slippage}%\n"
                f"Gas: {target_gas_price/1e9:.1f} Gwei"
            )
            
            return True
        else:
            logger.error(f"卖出失败: {res.get('reason')}")
            await self._send_telegram_notification(f"❌ 卖出失败 {pos.token_name}: {res.get('reason')}")
            return False

    async def _check_take_profit(self, pos: Position) -> bool:
        """策略一：分批止盈"""
        levels = self.config.get("take_profit", {}).get("levels", [])
        # levels: [[100, 25], [200, 25]...]
        
        for threshold, sell_pct in levels:
            # 检查是否已经触发过该档位
            level_tag = f"tp_{threshold}"
            if any(r.get("reason") == level_tag for r in pos.sold_portions):
                continue
                
            if pos.pnl_percentage >= threshold:
                await self._execute_sell(pos, sell_pct, level_tag)
                return True # 每次循环只触发一个动作
                
        return False

    async def _check_time_stop_loss(self, pos: Position) -> bool:
        """策略三：时间止损"""
        rules = self.config.get("time_stop", {}).get("rules", [])
        # rules: [[6, 20, 50], [24, 50, 100]...]
        
        hours_held = (time.time() - pos.buy_time) / 3600
        
        for hours, min_pnl, sell_pct in rules:
            rule_tag = f"time_stop_{hours}h"
            # 检查是否已执行过
            if any(r.get("reason") == rule_tag for r in pos.sold_portions):
                continue
            
            # 如果持有时间超过规定时间
            if hours_held >= hours:
                # 且涨幅低于 min_pnl
                if pos.pnl_percentage < min_pnl:
                    await self._execute_sell(pos, sell_pct, rule_tag)
                    return True
                    
        return False

    def _check_daily_reset(self):
        """检查是否需要重置每日统计"""
        today = datetime.now().strftime("%Y-%m-%d")
        if self.daily_stats["date"] != today:
            self.daily_stats = {
                "date": today,
                "buy_count": 0,
                "sell_count": 0,
                "win_count": 0,
                "loss_count": 0,
                "profit_bnb": 0.0,
                "loss_bnb": 0.0
            }
            logger.info("每日统计已重置")

    def _check_daily_risk_allow_buy(self) -> bool:
        """策略四：每日风控 - 是否允许买入"""
        cfg = self.config.get("daily_risk", {})
        max_loss = cfg.get("max_daily_loss", 0.5)
        
        net_pnl = self.daily_stats["profit_bnb"] - self.daily_stats["loss_bnb"]
        
        # 如果净亏损超过阈值 (net_pnl 是负数， abs(net_pnl) > max_loss)
        if net_pnl < 0 and abs(net_pnl) > max_loss:
            return False
            
        return True
        
    def get_suggested_buy_amount(self, default_amount: float) -> float:
        """策略四：每日风控 - 获取建议买入金额"""
        cfg = self.config.get("daily_risk", {})
        profit_threshold = cfg.get("profit_threshold_conservative", 1.0)
        
        net_pnl = self.daily_stats["profit_bnb"] - self.daily_stats["loss_bnb"]
        
        # 如果盈利超过阈值，保守模式，减半投入
        if net_pnl > profit_threshold:
            return default_amount * 0.5
            
        return default_amount

    def _update_daily_stats(self, buy=False, profit=0.0, loss=0.0):
        """更新每日统计"""
        if buy:
            self.daily_stats["buy_count"] += 1
        else:
            self.daily_stats["sell_count"] += 1
            self.daily_stats["profit_bnb"] += profit
            self.daily_stats["loss_bnb"] += loss
            
            if profit > 0:
                self.daily_stats["win_count"] += 1
            elif loss > 0: # or profit < 0
                self.daily_stats["loss_count"] += 1


    def _log_dashboard(self):
        """打印仓位统计面板"""
        net_pnl = self.daily_stats["profit_bnb"] - self.daily_stats["loss_bnb"]
        
        lines = []
        lines.append("─────────────────────────────────")
        lines.append(f"📊 当前持仓状态 {datetime.now().strftime('%Y-%m-%d %H:%M')}")
        lines.append("─────────────────────────────────")
        lines.append(f"{'代币':<8} {'买入价':<10} {'当前价':<10} {'盈亏':<8} {'状态':<10}")
        
        for pos in self.positions.values():
            status_icon = "✅持有中"
            if pos.pnl_percentage < -20: status_icon = "⚠️亏损中"
            if pos.pnl_percentage > 50: status_icon = "🚀盈利中"
            
            lines.append(f"{pos.token_name:<8} {pos.buy_price_bnb:<10.6f} {pos.current_price:<10.6f} {pos.pnl_percentage:>+6.1f}% {status_icon}")
            
        lines.append("─────────────────────────────────")
        lines.append(f"今日统计：买入{self.daily_stats['buy_count']}次 | 盈利{self.daily_stats['profit_bnb']:.4f}BNB | 亏损{self.daily_stats['loss_bnb']:.4f}BNB | 净盈亏{net_pnl:+.4f}BNB")
        lines.append("─────────────────────────────────")
        
        logger.info("\n" + "\n".join(lines))

async def price_monitor_loop():
    """
    每20秒查询一次所有活跃持仓的最新价格
    这个函数必须作为独立的asyncio任务运行，不能被其他逻辑阻塞
    """
    while True:
        try:
            # 1. 从数据库获取所有status='active'的持仓
            active_positions = await get_active_positions()
            
            if not active_positions:
                await asyncio.sleep(20)
                continue
            
            # 2. 并行查询所有持仓的价格（不要串行，太慢）
            price_tasks = [
                get_token_price(pos.token_address) 
                for pos in active_positions
            ]
            prices = await asyncio.gather(*price_tasks, return_exceptions=True)
            
            # 3. 逐个更新价格并检查止盈止损
            for pos, price_result in zip(active_positions, prices):
                
                # 如果价格查询失败，跳过这个币，不要用0覆盖
                if isinstance(price_result, Exception):
                    logger.warning(f"查询{pos.token_name}价格失败: {price_result}")
                    continue
                    
                if price_result is None or price_result.get('price_bnb', 0) == 0:
                    logger.warning(f"{pos.token_name}价格返回0，跳过本次更新")
                    continue  # 关键！价格为0时绝对不能更新，更不能触发止损
                
                current_price = price_result['price_bnb']
                
                # 4. 更新数据库中的现价
                await update_position_price(pos.token_address, current_price)
                
                # 5. 计算盈亏
                pnl_pct = (current_price - pos.buy_price_bnb) / pos.buy_price_bnb * 100
                
                logger.info(f"{pos.token_name}: 现价={current_price:.8f} 买入价={pos.buy_price_bnb:.8f} 盈亏={pnl_pct:+.1f}%")
                
                # 6. 检查止盈止损（只有价格有效才检查）
                await check_take_profit_stop_loss(pos, current_price, pnl_pct)
                
        except Exception as e:
            logger.error(f"价格监控循环异常: {e}")
            # 异常后等待，但不要退出循环！
            
        await asyncio.sleep(20)  # 每20秒一次


_bnb_price_cache = {"price": 600.0, "ts": 0.0}  # 模块级BNB价格缓存

async def get_bnb_price() -> float:
    """
    获取BNB/USD价格，优先使用缓存（TTL=60s）。
    从多个公开API尝试，全部失败时返回上次缓存值（默认600.0）。
    """
    cache = _bnb_price_cache
    if time.time() - cache["ts"] < 60:
        return cache["price"]

    urls = [
        "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT",
        "https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd",
    ]
    async with aiohttp.ClientSession() as session:
        for url in urls:
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if "price" in data:
                            price = float(data["price"])
                        elif "binancecoin" in data:
                            price = float(data["binancecoin"]["usd"])
                        else:
                            continue
                        cache["price"] = price
                        cache["ts"] = time.time()
                        return price
            except Exception:
                continue

    # 全部失败，返回上次缓存值
    logger.debug(f"BNB price APIs unavailable, using cached value: {cache['price']}")
    return cache["price"]


async def get_token_price(token_address: str) -> dict:
    """
    双数据源查询价格，任一成功即返回
    优先用链上数据，备用DexScreener API
    """
    
    # 方法一：链上查询（最准确）
    try:
        router = web3.eth.contract(address=ROUTER_ADDRESS, abi=ROUTER_ABI)
        wbnb_address = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
        
        # 用1个代币查询能换多少WBNB
        amounts = router.functions.getAmountsOut(
            10**18,  # 1个代币（18位小数）
            [token_address, wbnb_address]
        ).call()
        
        price_bnb = amounts[1] / 10**18
        
        if price_bnb > 0:
            return {'price_bnb': price_bnb, 'source': 'onchain'}
            
    except Exception as e:
        logger.debug(f"链上查询失败: {e}")
    
    # 方法二：DexScreener API（备用）
    try:
        url = f"https://api.dexscreener.com/latest/dex/tokens/{token_address}"
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=5)) as resp:
                data = await resp.json()
                
                if data.get('pairs') and len(data['pairs']) > 0:
                    pair = data['pairs'][0]
                    price_usd = float(pair.get('priceUsd', 0))
                    bnb_price_usd = await get_bnb_price()
                    
                    if price_usd > 0 and bnb_price_usd > 0:
                        price_bnb = price_usd / bnb_price_usd
                        return {'price_bnb': price_bnb, 'source': 'dexscreener'}
                        
    except Exception as e:
        logger.debug(f"DexScreener查询失败: {e}")
    
    # 两个都失败，返回None（不返回0！）
    return None

async def check_take_profit_stop_loss(position, current_price: float, pnl_pct: float):
    """
    只有在价格有效的情况下才检查止盈止损
    """
    
    # 安全检查：价格必须合理（不能是0，不能比买入价低99%以上）
    if current_price <= 0:
        logger.error(f"价格异常为0，跳过止损检查！{position.token_name}")
        return
    
    # 防止误触发：跌幅超过99%时先确认流动性是否真的没了
    if pnl_pct < -95:
        logger.warning(f"{position.token_name}跌幅异常({pnl_pct:.1f}%)，二次确认中...")
        await asyncio.sleep(10)
        
        # 10秒后再查一次
        confirm_price = await get_token_price(position.token_address)
        if confirm_price is None or confirm_price['price_bnb'] <= 0:
            logger.warning(f"{position.token_name}二次确认价格仍异常，可能流动性已移除，执行止损")
            await execute_sell(position.token_address, 100, "流动性移除止损")
            return
        
        # 用确认后的价格重新计算
        current_price = confirm_price['price_bnb']
        pnl_pct = (current_price - position.buy_price_bnb) / position.buy_price_bnb * 100
        
        if pnl_pct > -95:
            logger.info(f"{position.token_name}二次确认正常，价格恢复，不触发止损")
            return
    
    # 更新最高价记录
    if current_price > position.highest_price:
        await update_highest_price(position.token_address, current_price)
        position.highest_price = current_price
    
    # 追踪止损检查（从最高点回落超过40%）
    if position.highest_price > 0:
        drawdown = (position.highest_price - current_price) / position.highest_price * 100
        
        # 动态止损线
        max_pnl = (position.highest_price - position.buy_price_bnb) / position.buy_price_bnb * 100
        if max_pnl > 400:    # 曾经涨过5倍
            stop_drawdown = 40
        elif max_pnl > 200:  # 曾经涨过3倍
            stop_drawdown = 45
        else:
            stop_drawdown = 50  # 默认止损
        
        if drawdown >= stop_drawdown:
            await execute_sell(position.token_address, 100, f"追踪止损(从最高点回落{drawdown:.1f}%)")
            return
    
    # 固定止损（买入后30分钟内）
    holding_minutes = (datetime.now() - position.buy_time).seconds / 60
    if holding_minutes < 30 and pnl_pct < -50:
        await execute_sell(position.token_address, 100, f"早期止损({pnl_pct:.1f}%)")
        return
    
    # 分批止盈检查
    take_profit_levels = [
        (100, 25, "止盈第1档2倍"),
        (200, 25, "止盈第2档3倍"),
        (400, 25, "止盈第3档5倍"),
        (900, 15, "止盈第4档10倍"),
    ]
    
    for target_pct, sell_pct, reason in take_profit_levels:
        level_key = f"tp_{target_pct}"
        if pnl_pct >= target_pct and level_key not in position.sold_portions:
            await execute_sell(position.token_address, sell_pct, reason)
            await mark_portion_sold(position.token_address, level_key)
            break
