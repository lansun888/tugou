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
from web3 import AsyncWeb3, Web3
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
    pair_address: str = "" # Add pair_address
    current_price: float = 0.0
    current_value_bnb: float = 0.0
    pnl_percentage: float = 0.0
    highest_price: float = 0.0
    sold_portions: List[Dict] = field(default_factory=list)
    status: str = "active" # active, closed
    buy_gas_price: int = 0  # Gas Price at buy time (wei)
    fetch_fail_count: int = 0 # Consecutive price fetch failures
    last_update_time: float = 0.0 # Timestamp of last price update
    dex_name: str = None # DEX Name (pancakeswap_v2, four_meme, etc.)
    security_score: int = 0  # 安全评分（买入时记录）
    
    # DexScreener Data
    volume_24h: float = 0.0
    price_change_5m: float = 0.0
    market_cap: float = 0.0
    txns_5m_buys: int = 0
    txns_5m_sells: int = 0
    source: str = "init"
    
    # Flags
    first_check_done: bool = False # Flag for 2-min quick assessment
    passed_honeypot_check: bool = False  # 延迟貔貅检测标记（2分钟时触发一次）
    has_real_price: bool = False  # 是否已获取过真实市场价（非买入估算价）
    tp_100_done: bool = False
    tp_200_done: bool = False
    tp_400_done: bool = False
    tp_900_done: bool = False
    trailing_stop_pct: float = 0.0  # 动态追踪止损线
    highest_pnl: float = 0.0        # 历史最高盈利百分比
    liquidity_bnb: float = 0.0          # 最近一次流动性（BNB）
    initial_liquidity_bnb: float = 0.0  # 买入时初始流动性（用于2分钟评估）
    
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

        if self.pnl_percentage > self.highest_pnl:
            self.highest_pnl = self.pnl_percentage

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
        self._pair_token0_cache: Dict[str, str] = {} # pair_address -> token0 缓存
        self._token_decimals_cache: Dict[str, int] = {} # token_address -> decimals 缓存
        
        # Lock for duplicate sell prevention (in-memory)
        self.selling_tokens = set()
        
        # Track pending stop losses for "N consecutive confirmations"
        # Format: {token_address: {"count": int, "first_trigger_time": float}}
        self.pending_stop_loss = {}
        
        # Track active event monitoring tasks
        # Format: {token_address: asyncio.Task}
        self.watch_tasks = {}
        
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
                    pair_address TEXT DEFAULT '',
                    current_price REAL DEFAULT 0.0,
                    pnl_percentage REAL DEFAULT 0.0,
                    volume_24h REAL DEFAULT 0.0,
                    price_change_5m REAL DEFAULT 0.0,
                    market_cap REAL DEFAULT 0.0,
                    txns_5m_buys INTEGER DEFAULT 0,
                    txns_5m_sells INTEGER DEFAULT 0,
                    source TEXT DEFAULT 'init',
                    initial_liquidity_bnb REAL DEFAULT 0.0,
                    security_score INTEGER DEFAULT 0
                )
            """)

            # Migration check
            try:
                cursor = await db.execute(f"PRAGMA table_info({self.positions_table})")
                columns = [row[1] for row in await cursor.fetchall()]

                if "security_score" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN security_score INTEGER DEFAULT 0")
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
                if "pair_address" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN pair_address TEXT DEFAULT ''")
                if "initial_liquidity_bnb" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN initial_liquidity_bnb REAL DEFAULT 0.0")
                if "dex_name" not in columns:
                    await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN dex_name TEXT")
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
                    if "pair_address" not in columns:
                        logger.info(f"Migrating {self.positions_table} table: adding pair_address")
                        await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN pair_address TEXT DEFAULT ''")
                    if "dex_name" not in columns:
                        logger.info(f"Migrating {self.positions_table} table: adding dex_name")
                        await db.execute(f"ALTER TABLE {self.positions_table} ADD COLUMN dex_name TEXT")
                except Exception as e2:
                    logger.warning(f"Legacy migration also failed: {e2}")
                
            await db.commit()

    async def _load_positions(self):
        """从数据库恢复活跃仓位"""
        try:
            logger.info(f"Loading positions from {self.db_path} table {self.positions_table}")
            async with aiosqlite.connect(self.db_path) as db:
                db.row_factory = aiosqlite.Row
                
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
                            pair_address=row_dict.get('pair_address', '') or '',
                            current_price=row_dict.get('current_price', 0.0) or 0.0,
                            pnl_percentage=row_dict.get('pnl_percentage', 0.0) or 0.0,
                            initial_liquidity_bnb=row_dict.get('initial_liquidity_bnb', 0.0) or 0.0,
                            dex_name=row_dict.get('dex_name')
                        )
                        # Recalculate values if needed
                        pos.current_value_bnb = pos.token_amount * pos.current_price
                        self.positions[pos.token_address] = pos
                        
                        # Restart monitoring if pair_address is available
                        if pos.pair_address:
                            self.start_watching(pos.token_address, pos.pair_address)
                            
            logger.info(f"恢复了 {len(self.positions)} 个活跃仓位")
        except Exception as e:
            logger.error(f"恢复仓位失败: {e}")

    async def add_position(self, token_address, token_name, buy_price, buy_amount_bnb, token_amount, buy_gas_price=0, dex_data=None, pair_address="", initial_liquidity_bnb=0.0, dex_name=None, security_score=0):
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
            buy_gas_price=buy_gas_price,
            pair_address=pair_address,
            initial_liquidity_bnb=initial_liquidity_bnb,
            liquidity_bnb=initial_liquidity_bnb,
            dex_name=dex_name,
            security_score=security_score
        )
        
        # ===== Fix for Four.meme: Fetch pair address immediately =====
        if dex_name == 'four_meme' and not pair_address:
            try:
                PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
                WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
                
                factory = self.executor.w3.eth.contract(
                    address=PANCAKE_FACTORY,
                    abi=PANCAKESWAP_V2_FACTORY_ABI
                )
                fetched_pair = await factory.functions.getPair(
                    token_address, WBNB
                ).call()
                
                if fetched_pair == '0x' + '0'*40:
                    # Retry once after 2s
                    await asyncio.sleep(2)
                    fetched_pair = await factory.functions.getPair(
                        token_address, WBNB
                    ).call()
                
                if fetched_pair and fetched_pair != '0x' + '0'*40:
                    logger.info(f"[AddPosition] Four.meme pair found: {fetched_pair}")
                    pos.pair_address = fetched_pair
            except Exception as e:
                logger.warning(f"[AddPosition] Failed to fetch pair for {token_name}: {e}")
        
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
        
        # Start monitoring
        if pair_address:
            self.start_watching(token_address, pair_address)
        
        # 更新每日统计
        self._update_daily_stats(buy=True)
        return True

    async def _save_position(self, pos: Position):
        """保存仓位到数据库"""
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(f"""
                    INSERT OR REPLACE INTO {self.positions_table}
                    (token_address, token_name, buy_price_bnb, buy_amount_bnb, token_amount, buy_time, highest_price, sold_portions, status, buy_gas_price, pair_address, current_price, pnl_percentage, initial_liquidity_bnb, dex_name, security_score)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    pos.token_address, pos.token_name, pos.buy_price_bnb, pos.buy_amount_bnb,
                    pos.token_amount, pos.buy_time, pos.highest_price, json.dumps(pos.sold_portions), pos.status, pos.buy_gas_price,
                    pos.pair_address, pos.current_price, pos.pnl_percentage, pos.initial_liquidity_bnb, pos.dex_name, pos.security_score
                ))
                await db.commit()
        except Exception as e:
            logger.error(f"保存仓位失败: {e}")

    def start_watching(self, token_address, pair_address):
        """启动单个币种的事件监听"""
        if token_address in self.watch_tasks:
            return # Already watching
            
        task = asyncio.create_task(self.watch_swap_events(token_address, pair_address))
        self.watch_tasks[token_address] = task
        logger.info(f"已为 {token_address[:8]} 启动实时价格监听 (Event-Driven)")

    def stop_watching(self, token_address):
        """停止监听"""
        if token_address in self.watch_tasks:
            self.watch_tasks[token_address].cancel()
            del self.watch_tasks[token_address]
            logger.info(f"停止监听 {token_address[:8]}")

    async def watch_swap_events(self, token_address: str, pair_address: str):
        """
        监听指定交易对的Swap事件
        每次有Swap发生就立即更新价格
        """
        SWAP_EVENT_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
        # Fix: Access network config from executor.config, not self.config (which is position_management)
        ws_url = self.executor.config.get("network", {}).get("ws_node")
        
        if not ws_url:
            logger.warning(f"[{token_address}] 未配置 ws_node，无法启动事件监听")
            return

        while self.running:
            try:
                # Use WebSocketProvider (v7 compatible)
                async with AsyncWeb3(AsyncWeb3.WebSocketProvider(ws_url)) as w3_ws:
                    # Inject POA middleware for BSC
                    from web3.middleware import ExtraDataToPOAMiddleware
                    w3_ws.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                    
                    if not await w3_ws.is_connected():
                        logger.error(f"[{token_address}] WS连接失败")
                        await asyncio.sleep(5)
                        continue

                    # 订阅
                    subscription_id = await w3_ws.eth.subscribe('logs', {
                        'address': pair_address,
                        'topics': [SWAP_EVENT_TOPIC]
                    })
                    
                    logger.info(f"监听就绪: {token_address[:8]} (SubID: {subscription_id})")
                    
                    # 监听循环
                    async for response in w3_ws.socket.process_subscriptions():
                        try:
                            start_time = time.time()
                            # Swap发生了，立即重新计算价格
                            # Pass cache_only=False to force re-fetch reserves
                            new_price_info, err = await self._get_price_onchain(token_address, pair_address)
                            
                            if new_price_info:
                                new_price = new_price_info.get('price_bnb', 0.0)
                                liquidity = new_price_info.get('liquidity_bnb', 0.0)
                                
                                if new_price > 0:
                                    pos = self.positions.get(token_address)
                                    # Double check status
                                    if not pos or pos.status not in ['active', 'partially_sold']:
                                        return # Stop loop
                                        
                                    # Update & Check Strategies IMMEDIATELY
                                    await self._update_position_price(pos, new_price, 'swap_event', liquidity)
                                    await self._check_strategies(pos, new_price, liquidity)
                                    
                                    # 记录耗时 (超过200ms才打印，避免刷屏)
                                    elapsed = (time.time() - start_time) * 1000
                                    if elapsed > 200:
                                        logger.debug(f"⚡ [Event] {pos.token_name} 价格更新完成 | 耗时: {elapsed:.2f}ms | 来源: Swap事件")
                                    
                        except Exception as inner_e:
                            logger.error(f"处理Swap事件异常: {inner_e}")
                            
            except asyncio.CancelledError:
                logger.info(f"监听任务被取消: {token_address[:8]}")
                break
            except Exception as e:
                logger.error(f"Swap监听连接断开 ({token_address}): {e}, 5秒后重连...")
                await asyncio.sleep(5)
            
            # Check if we should stop loop (redundant with CancelledError but safe)
            pos = self.positions.get(token_address)
            if not pos or pos.status not in ['active', 'partially_sold']:
                break

    async def start_monitoring(self):
        """启动监控循环 (Fallback Polling + Task Health Check)"""
        self.running = True
        logger.info("启动仓位监控循环 (Fallback Polling, 30s interval)...")
        
        from utils.dexscreener_client import get_batch_prices
        
        last_dashboard_time = 0
        monitor_interval = 30 # Slower interval for fallback
        
        while self.running:
            try:
                # 1. 每日统计重置
                self._check_daily_reset()
                
                # 2. Check Watch Tasks Health
                for token_address, task in list(self.watch_tasks.items()):
                    if task.done():
                        # Check exception
                        try:
                            exc = task.exception()
                            if exc:
                                logger.warning(f"{token_address[:8]} 事件监听异常断开: {exc}")
                        except:
                            pass
                            
                        pos = self.positions.get(token_address)
                        if pos and pos.pair_address and pos.status in ['active', 'partially_sold']:
                            logger.warning(f"{token_address[:8]} 事件监听已停止，尝试重启...")
                            new_task = asyncio.create_task(
                                self.watch_swap_events(token_address, pos.pair_address)
                            )
                            self.watch_tasks[token_address] = new_task
                        else:
                            # 正常停止 (已卖出或移除)，清理任务
                            if token_address in self.watch_tasks:
                                del self.watch_tasks[token_address]
                
                # 3. Get active tokens
                active_tokens = list(self.positions.keys())
                if not active_tokens:
                    await asyncio.sleep(monitor_interval)
                    continue
                
                # 4. Batch Query (DexScreener) - Fallback
                all_prices = {}
                try:
                    all_prices = await get_batch_prices(active_tokens)
                except Exception as e:
                    logger.warning(f"Batch price fetch failed (Network Issue?): {e}")
                    # Proceed to individual fallback
                
                # 5. Process each position in PARALLEL
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

    async def _process_single_token(self, token_address, dex_data=None):
        """处理单个代币的价格更新和策略检查（并行安全版）"""
        pos = self.positions.get(token_address)
        if not pos or pos.status in ("sold", "closed"):
            return

        start_time = time.time()

        price = None
        price_source = None
        liquidity_bnb = 0.0

        # ===== Four.meme代币：bonding curve链上价格 =====
        if pos.dex_name == 'four_meme':
            price_info, _ = await self._get_four_meme_price_onchain(token_address)
            if price_info:
                price = price_info.get('price_bnb')
                liquidity_bnb = price_info.get('liquidity_bnb', 0.0)
                price_source = price_info.get('source', 'four_meme_onchain')

            if not price or price <= 0:
                # 使用历史价格（最多600秒内有效）
                STALE_PRICE_MAX_AGE = 600
                last_price = pos.current_price
                age = time.time() - pos.last_update_time if pos.last_update_time > 0 else float('inf')
                if last_price > 0 and age < STALE_PRICE_MAX_AGE:
                    logger.debug(f"{pos.token_name}: 使用历史价格 {last_price:.2e} BNB (陈旧 {age:.0f}s)")
                    await self._check_strategies(pos, last_price, pos.liquidity_bnb)
                else:
                    pos.fetch_fail_count += 1
                    logger.debug(f"{pos.token_name}: 价格获取失败且无可用历史价格，跳过")
                return

        # ===== 普通代币：优先DexScreener，失败降级链上 =====
        else:
            # 先用批量查询结果
            if dex_data and dex_data.get(token_address.lower()):
                pair_data = dex_data.get(token_address.lower())
                price = pair_data.get('price_bnb')
                liquidity_bnb = pair_data.get('liquidity_bnb', 0.0)
                # Update other stats
                pos.volume_24h = pair_data.get('volume_24h', 0.0)
                pos.price_change_5m = pair_data.get('price_change_5m', 0.0)
                pos.market_cap = pair_data.get('market_cap', 0.0)
                pos.txns_5m_buys = pair_data.get('txns_5m_buys', 0)
                pos.txns_5m_sells = pair_data.get('txns_5m_sells', 0)
                price_source = 'dexscreener'
            
            # DexScreener没有数据，降级到链上（PancakeSwap V2 pair reserves）
            if price is None:
                price = await self._get_price_from_reserves(
                    token_address, pos.pair_address
                )
                price_source = 'onchain_fallback'

            if price is None:
                logger.warning(f"{pos.token_name}: 价格获取失败，跳过")
                pos.fetch_fail_count += 1
                return

        pos.fetch_fail_count = 0
        logger.debug(f"{pos.token_name}: 价格={price:.4e} 来源={price_source}")
        await self._update_position_price(pos, price, price_source, liquidity_bnb)
        await self._check_strategies(pos, price, liquidity_bnb)
        
        elapsed = (time.time() - start_time) * 1000
        if elapsed > 500:
             logger.debug(f"🔄 [Poll] {pos.token_name} 价格更新完成 | 耗时: {elapsed:.2f}ms | 来源: {price_source}")

    async def _ensure_pair_address(self, pos):
        """Ensure pair address exists for Four.meme tokens"""
        if pos.pair_address:
            return
            
        try:
            PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
            WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
            FACTORY_ABI = [{"constant":True,"inputs":[{"internalType":"address","name":"","type":"address"},{"internalType":"address","name":"","type":"address"}],"name":"getPair","outputs":[{"internalType":"address","name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}]
            
            factory = self.executor.w3.eth.contract(
                address=PANCAKE_FACTORY,
                abi=FACTORY_ABI
            )
            pair_address = await factory.functions.getPair(
                pos.token_address, WBNB
            ).call()
            
            if pair_address and pair_address != '0x' + '0'*40:
                logger.info(f"Found missing pair address for {pos.token_name}: {pair_address}")
                pos.pair_address = pair_address
                # Save to DB
                await self._save_position(pos)
        except Exception as e:
            logger.warning(f"Failed to fetch pair address for {pos.token_name}: {e}")

    # decimals缓存，避免重复查询
    _decimals_cache = {}

    async def _get_token_decimals(self, token_address: str) -> int:
        if token_address in self._decimals_cache:
            return self._decimals_cache[token_address]
        
        try:
            ERC20_ABI_DECIMALS = [{
                "name": "decimals",
                "outputs": [{"name": "", "type": "uint8"}],
                "stateMutability": "view",
                "type": "function"
            }]
            contract = self.executor.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI_DECIMALS
            )
            decimals = await contract.functions.decimals().call()
            self._decimals_cache[token_address] = decimals
            return decimals
        except:
            return 18  # 默认18位

    async def _get_price_from_reserves(
        self, token_address: str, pair_address: str
    ) -> float | None:
        
        try:
            # pair地址无效直接返回
            if not pair_address:
                logger.debug(f"pair_address为空: {token_address[:8]}")
                return None
            
            if pair_address == '0x' + '0' * 40:
                logger.debug(f"pair_address是零地址: {token_address[:8]}")
                return None
            
            PAIR_ABI = [
                {
                    "name": "getReserves",
                    "outputs": [
                        {"name": "reserve0", "type": "uint112"},
                        {"name": "reserve1", "type": "uint112"},
                        {"name": "blockTimestampLast", "type": "uint32"}
                    ],
                    "stateMutability": "view",
                    "type": "function"
                },
                {
                    "name": "token0",
                    "outputs": [{"name": "", "type": "address"}],
                    "stateMutability": "view",
                    "type": "function"
                }
            ]
            
            WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
            
            pair = self.executor.w3.eth.contract(
                address=Web3.to_checksum_address(pair_address),
                abi=PAIR_ABI
            )
            
            async with asyncio.timeout(3):
                reserves, token0 = await asyncio.gather(
                    pair.functions.getReserves().call(),
                    pair.functions.token0().call()
                )
            
            reserve0, reserve1, _ = reserves
            
            if token0.lower() == WBNB.lower():
                bnb_reserve = reserve0
                token_reserve = reserve1
            else:
                bnb_reserve = reserve1
                token_reserve = reserve0
            
            if token_reserve == 0 or bnb_reserve == 0:
                return None
            
            # 获取decimals（带缓存）
            decimals = await self._get_token_decimals(token_address)
            
            price = (bnb_reserve / 10**18) / (token_reserve / 10**decimals)
            return price
            
        except asyncio.TimeoutError:
            logger.debug(f"getReserves超时: {token_address[:8]}")
            return None
        except Exception as e:
            logger.debug(f"getReserves异常: {token_address[:8]}: {e}")
            return None

    async def _update_position_price(self, pos, price_bnb, source, liquidity_bnb):
        """Helper to update position price"""
        pos.update_price(price_bnb)
        pos.source = source
        # 标记已获取真实市场价（非买入时的估算价）
        if source not in ("init",) and price_bnb > 0:
            pos.has_real_price = True
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

        now = time.time()
        pos.liquidity_bnb = liquidity_bnb
        holding_minutes = (now - pos.buy_time) / 60
        pnl = pos.pnl_percentage

        # ===== 阶段一：0-2分钟保护期 =====
        if holding_minutes < 2:
            logger.debug(f"{pos.token_name}: 保护期 "
                        f"{holding_minutes:.1f}分钟 pnl={pnl:.1f}%")
            if 0 < liquidity_bnb < 0.5:
                await self._execute_sell(pos, 100, 'rug_pull_confirmed')
                return
            if pnl < -70:
                await self._execute_sell(pos, 100, 'emergency_crash')
                return
            return

        # ===== 阶段二：2分钟时，延迟貔貅检测 =====
        if not pos.passed_honeypot_check:
            # 2分钟评估要求真实市场价（has_real_price=True）。
            # 买入时的估算价（totalSupply推算）不代表市场行情，用它评估会导致误判。
            if not pos.has_real_price:
                logger.debug(f"{pos.token_name}: 2分钟评估延迟，尚未获取真实市场价")
                # 不标记 passed_honeypot_check，下轮价格到位后再评估
                return

            pos.passed_honeypot_check = True  # 只触发一次

            init_liq = pos.initial_liquidity_bnb
            liq_change_pct = ((liquidity_bnb - init_liq) / init_liq * 100) if init_liq > 0 else 0.0

            logger.info(f"{pos.token_name}: 2分钟评估 "
                       f"pnl={pnl:.1f}% liq_init={init_liq:.2f} liq_now={liquidity_bnb:.2f} "
                       f"liq_chg={liq_change_pct:.1f}%")

            # 情况一：流动性撤出 > 30% → 疑似貔貅，离场
            if liq_change_pct < -30:
                logger.info(f"{pos.token_name}: 2分钟流动性暴跌 liq_chg={liq_change_pct:.1f}%，离场")
                await self._execute_sell(pos, 100, 'liq_drain_2min')
                return

            # 情况二：pnl < 20% 且 流动性增长 < 10% → 无热度，离场
            # four_meme: init_liq=0（bonding curve 无初始流动性记录），跳过流动性维度，仅看 pnl
            is_four_meme = (pos.dex_name == "four_meme")
            if pnl < 20 and (liq_change_pct < 10 or is_four_meme and init_liq == 0):
                if is_four_meme and init_liq == 0:
                    # four_meme 无初始流动性基准，只靠 pnl 判断
                    if pnl < 20:
                        logger.info(f"{pos.token_name}: [four_meme] 2分钟pnl={pnl:.1f}%不足，离场")
                        await self._execute_sell(pos, 100, 'no_momentum_2min')
                        return
                else:
                    logger.info(f"{pos.token_name}: 2分钟无热度 "
                               f"pnl={pnl:.1f}% liq_chg={liq_change_pct:.1f}%，离场")
                    await self._execute_sell(pos, 100, 'no_momentum_2min')
                    return

            # 情况三：pnl >= 20% 或 流动性增长 >= 10% → 有热度，继续持有
            logger.info(f"{pos.token_name}: 2分钟评估通过，继续持有")

        # ===== 阶段三：正常止盈止损 =====

        # 止盈检查
        if pnl >= 900 and not pos.tp_900_done:
            await self._execute_sell(pos, 15, 'tp_900')
            pos.tp_900_done = True
            return
        if pnl >= 400 and not pos.tp_400_done:
            await self._execute_sell(pos, 25, 'tp_400')
            pos.tp_400_done = True
            return
        if pnl >= 200 and not pos.tp_200_done:
            await self._execute_sell(pos, 25, 'tp_200')
            pos.tp_200_done = True
            return
        if pnl >= 100 and not pos.tp_100_done:
            await self._execute_sell(pos, 25, 'tp_100')
            pos.tp_100_done = True
            return

        # 追踪止损线更新
        if pnl >= 900:
            pos.trailing_stop_pct = 700
        elif pnl >= 400:
            pos.trailing_stop_pct = 300
        elif pnl >= 200:
            pos.trailing_stop_pct = 150
        elif pnl >= 100:
            pos.trailing_stop_pct = 50

        # 追踪止损检查
        if pos.trailing_stop_pct > 0 and pnl <= pos.trailing_stop_pct:
            await self._execute_sell(pos, 100, 'trailing_stop')
            return

        # 初始止损（2-30分钟内）
        if holding_minutes <= 30 and pnl <= -35:
            await self._execute_sell(pos, 100, 'initial_stop_loss')
            return

        # 回撤止损
        if pos.highest_pnl > 0:
            drawdown = pos.highest_pnl - pnl
            if drawdown >= 40:
                await self._execute_sell(pos, 100, 'drawdown_40')
                return

        # 时间止损
        if holding_minutes >= 6*60 and pnl < 20:
            await self._execute_sell(pos, 50, 'time_stop_6h')
            return
        if holding_minutes >= 24*60 and pnl < 50:
            await self._execute_sell(pos, 100, 'time_stop_24h')
            return
        if holding_minutes >= 72*60:
            await self._execute_sell(pos, 100, 'time_stop_72h')
            return

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
            
            if pair_address in self._pair_token0_cache:
                token0 = self._pair_token0_cache[pair_address]
            else:
                token0 = await pair_contract.functions.token0().call()
                self._pair_token0_cache[pair_address] = token0
            
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
            if token_address in self._token_decimals_cache:
                decimals = self._token_decimals_cache[token_address]
            else:
                token_contract = w3.eth.contract(address=AsyncWeb3.to_checksum_address(token_address), abi=ERC20_ABI)
                decimals = await token_contract.functions.decimals().call()
                self._token_decimals_cache[token_address] = decimals
            
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



    async def _get_four_meme_price_onchain(self, token_address: str) -> Tuple[Optional[dict], str]:
        """
        four.meme bonding curve 价格查询（链上直接计算）。

        通过 factory.0xe684626b(address) 读取13槽位结构体：
          slot10 = k_norm（常量积 k/1e18）
          slot11 = virtual_token_reserve（虚拟代币储备，随买入减少）
          slot8  = bnb_raised（已募集BNB，Wei单位）

        价格公式（constant product AMM）：
          virtual_bnb = k_norm * 1e18 / virtual_token
          price_bnb   = virtual_bnb / virtual_token = k_norm * 1e18 / virtual_token^2

        已链上验证 (2026-03-07):
          零状态初始价 ≈ 5.75e-9 BNB/token（市值约5.75 BNB，符合four.meme launch定价）
        """
        FACTORY = '0x5c952063c7fc8610FFDB798152D69F0B9550762b'
        SEL = 'e684626b'  # tokenInfo(address) → 13-slot struct（链上探测确认）

        try:
            w3 = self.executor.w3
            token_padded = '000000000000000000000000' + token_address[2:].lower()
            call_data = bytes.fromhex(SEL + token_padded)
            factory_addr = AsyncWeb3.to_checksum_address(FACTORY)

            result = await w3.eth.call({'to': factory_addr, 'data': call_data})

            if not result or len(result) < 12 * 32:
                raise ValueError("返回数据不足13槽")

            raw = result.hex()
            slot10 = int(raw[10 * 64:(10 + 1) * 64], 16)  # k_norm = k/1e18
            slot11 = int(raw[11 * 64:(11 + 1) * 64], 16)  # virtual_token_reserve
            slot8  = int(raw[8  * 64:(8  + 1) * 64], 16)  # bnb_raised (Wei)

            if slot11 == 0:
                return None, "onchain_empty"

            # price_bnb = k_norm * 1e18 / virtual_token^2
            price_bnb = (slot10 * 10 ** 18) / (slot11 ** 2)

            # actual BNB in bonding curve for liquidity estimate
            bnb_raised = slot8 / 10 ** 18
            # liquidity_bnb: at minimum the bnb raised; for bonding curve use virtual_bnb as proxy
            virtual_bnb = (slot10 * 10 ** 18 / slot11) / 10 ** 18  # virtual BNB in BNB units
            liquidity_bnb = max(bnb_raised, virtual_bnb) * 2

            if price_bnb > 0:
                logger.debug(
                    f"[four_meme] 链上价格 {token_address[:10]}: "
                    f"price={price_bnb:.4e} BNB, bnb_raised={bnb_raised:.4f}, "
                    f"virtual_bnb={virtual_bnb:.4f}"
                )
                return {
                    "price_bnb": price_bnb,
                    "liquidity_bnb": liquidity_bnb,
                    "source": "four_meme_onchain",
                }, "none"

        except Exception as e:
            logger.debug(f"[four_meme] 链上价格查询失败 {token_address[:10]}: {e}")

        # Fallback: DexScreener（代币已上线 DEX 时）
        try:
            from bsc_bot.utils.dexscreener_client import get_token_data
            dex_data = await get_token_data(token_address)
            if dex_data and dex_data.get("price_bnb", 0) > 0:
                return {
                    "price_bnb": dex_data["price_bnb"],
                    "liquidity_bnb": dex_data.get("liquidity_bnb", 0.0),
                    "source": "dexscreener",
                }, "none"
        except Exception as e:
            logger.debug(f"[four_meme] DexScreener查询异常 {token_address[:10]}: {e}")

        return None, "onchain_empty"

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
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=5)) as response:
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
            initial_sl = cfg.get("initial_stop_loss", -35)
            # Ensure initial_sl is negative to avoid positive PnL triggers
            if initial_sl > 0: initial_sl = -35
            
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
                    reason = "trailing_stop"
            
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
            
        # 2. Database History Check (5s cool-down)
        # Skip this check if it's a Rug Pull (emergency) OR Manual Sell
        is_emergency = "rug" in reason.lower() or "emergency" in reason.lower() or "manual" in reason.lower()
        
        if is_emergency:
            logger.warning(f"🚨 {pos.token_name} 触发紧急/手动卖出 ({reason})，跳过冷却检查")
        

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
        
        # 3. Calculate Real PnL Logic
        current_holding = pos.token_amount
        sold_history_amount = sum(item.get('amount', 0) for item in pos.sold_portions)
        total_initial_tokens = current_holding + sold_history_amount
        if total_initial_tokens == 0: total_initial_tokens = current_holding

        # 批量卖出逻辑 (如果卖出量 > 流动性 5%)
        sell_value_bnb = pos.current_value_bnb * (percentage / 100)

        if liquidity_bnb > 0 and sell_value_bnb > (liquidity_bnb * 0.05) and percentage > 99:
            logger.warning(f"卖出量 ({sell_value_bnb:.2f} BNB) 超过流动性 5%，启用分批卖出")
            
            # Batch 1: 50% of current holding
            sell_amount_1 = current_holding * 0.5
            real_pct_1 = (sell_amount_1 / total_initial_tokens) * 100
            cost_basis_1 = pos.buy_amount_bnb * (real_pct_1 / 100)
            
            res1 = await self.executor.sell_token(
                pos.token_address, pos.token_name, 50,
                slippage=slippage, gas_price=target_gas_price,
                simulated_balance=pos.token_amount,
                manual_price=pos.current_price,
                sell_percentage_real=real_pct_1,
                cost_basis_bnb=cost_basis_1,
                dex_name=pos.dex_name
            )
            await asyncio.sleep(5)
            
            # Batch 2: 100% of remaining (which is other 50% of original current holding)
            remaining_sim = pos.token_amount * 0.5
            sell_amount_2 = remaining_sim
            real_pct_2 = (sell_amount_2 / total_initial_tokens) * 100
            cost_basis_2 = pos.buy_amount_bnb * (real_pct_2 / 100)
            
            res2 = await self.executor.sell_token(
                pos.token_address, pos.token_name, 100,
                slippage=slippage, gas_price=target_gas_price,
                simulated_balance=remaining_sim,
                manual_price=pos.current_price,
                sell_percentage_real=real_pct_2,
                cost_basis_bnb=cost_basis_2,
                dex_name=pos.dex_name
            )
            res = res2
            total_bnb = float(res1.get("amount_bnb", 0)) + float(res2.get("amount_bnb", 0))
            res["amount_bnb"] = total_bnb
        else:
            # 正常卖出
            sell_amount = current_holding * (percentage / 100)
            real_pct = (sell_amount / total_initial_tokens) * 100
            cost_basis = pos.buy_amount_bnb * (real_pct / 100)
            
            res = await self.executor.sell_token(
                pos.token_address, pos.token_name, percentage,
                slippage=slippage, gas_price=target_gas_price,
                simulated_balance=pos.token_amount,
                manual_price=pos.current_price,
                sell_percentage_real=real_pct,
                cost_basis_bnb=cost_basis,
                dex_name=pos.dex_name
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
        # rules: [[0.75, 0, 100], [6, 20, 50]...]
        
        hours_held = (time.time() - pos.buy_time) / 3600
        
        for hours, min_pnl, sell_pct in rules:
            rule_tag = "time_stop_loss" # Standardized tag as per user request
            
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

    async def _check_drawdown_stop_loss(self, pos: Position) -> bool:
        """策略五：回撤止损"""
        cfg = self.config.get("trailing_stop", {})
        pullback_limit = cfg.get("pullback_threshold", 40)
        
        if pos.highest_price > 0 and pos.highest_price > pos.buy_price_bnb * 1.1: # 至少涨一点
            pullback = (pos.highest_price - pos.current_price) / pos.highest_price * 100
            if pullback >= pullback_limit:
                 # Check N confirmation logic
                 token = pos.token_address
                 reason = "drawdown_40"
                 
                 if token not in self.pending_stop_loss:
                    self.pending_stop_loss[token] = {
                        "count": 1, 
                        "first_trigger_time": time.time(),
                        "reason": reason
                    }
                    logger.info(f"{pos.token_name} 触发回撤止损 ({reason})，等待二次确认...")
                    return False
                 else:
                    pending = self.pending_stop_loss[token]
                    if pending["reason"] != reason:
                        # Reason changed, reset
                        self.pending_stop_loss[token] = {
                            "count": 1, 
                            "first_trigger_time": time.time(),
                            "reason": reason
                        }
                        return False
                        
                    elapsed = time.time() - pending["first_trigger_time"]
                    
                    if elapsed > 15:
                        logger.info(f"{pos.token_name} 二次确认回撤止损 ({reason})，执行卖出!")
                        del self.pending_stop_loss[token]
                        await self._execute_sell(pos, 100, reason)
                        return True
                    else:
                        return False
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
        # Recalculate net_pnl: Only from realized trades (sell_count logic or explicit sum)
        # self.daily_stats['profit_bnb'] and ['loss_bnb'] are updated only on SELL in _update_daily_stats
        # So net_pnl here IS realized PnL.
        # But user reported issue: "买入0.8，卖出0.9216，显示+0.3433"
        # This implies +0.3433 = 0.1216 (Realized) + 0.2217 (Unrealized?)
        # Let's check _update_daily_stats logic.
        # _update_daily_stats is called with (buy=True) or (buy=False, profit=x, loss=y)
        # It seems correct for "Realized" only.
        # However, the user might be referring to the "Today's PnL" in the DASHBOARD API response, not this log.
        # The Dashboard API usually queries DB. Let's check `api_server.py` or wherever `/api/status` is handled.
        
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


