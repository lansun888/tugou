import asyncio
import logging
import os
import sys
import time
import yaml
from datetime import datetime
from loguru import logger
from dotenv import load_dotenv

# Use absolute imports from project root (assuming project root is in sys.path)
# web/api.py adds project root. main.py adds project root.
from bsc_bot.monitor.pair_listener import PairListener
from bsc_bot.analyzer.security_checker import SecurityChecker
from bsc_bot.analyzer.performance import PerformanceAnalyzer
from bsc_bot.executor.trader import BSCExecutor
from bsc_bot.executor.position_manager import PositionManager
from bsc_bot.simulation_manager import SimulationManager
from utils.dexscreener_client import get_token_data
# import web.api # Removed to avoid circular import
# Actually TradingBot logic doesn't use web.api. main.py uses it.

# Load environment variables
load_dotenv()

class TradingBot:
    def __init__(self, mode=None):
        self.base_dir = os.path.dirname(os.path.abspath(__file__))
        self.config_path = os.path.join(self.base_dir, "config.yaml")
        self.config = self.load_config()
        
        # Mode handling
        if mode:
            self.config['mode'] = mode
            
        self.mode = self.config.get('mode', 'live')
        logger.info(f"Initializing TradingBot in [{self.mode.upper()}] mode")
        
        self.db_path = os.path.join(self.base_dir, "data", "bsc_bot.db")
        
        # Components
        self.listener = PairListener(config_path=self.config_path, db_path=self.db_path)
        self.security_checker = SecurityChecker()
        self.executor = BSCExecutor(config_path=self.config_path, mode=self.mode)
        self.position_manager = PositionManager(self.executor, db_path=self.db_path, mode=self.mode)
        self.performance_analyzer = PerformanceAnalyzer(db_path=self.db_path)
        
        # Simulation Manager
        if self.mode == 'simulation':
             self.simulation_manager = SimulationManager(db_path=self.db_path)
        else:
             self.simulation_manager = None
             
        self.last_report_date = None
        
        self.running = False
        self.paused = False  # Pause new buys
        self.tasks = []

    def load_config(self):
        try:
            with open(self.config_path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"Failed to load config: {e}")
            sys.exit(1)

    async def check_network(self):
        """诊断网络连接情况（并行执行，更快）"""
        import aiohttp
        test_urls = [
            "https://api.dexscreener.com",
            "https://bsc-dataseed1.binance.org",
            "https://api.bscscan.com",
        ]
        logger.info("正在进行网络连接诊断...")

        async with aiohttp.ClientSession(trust_env=False) as s:
            async def _check_url(url):
                try:
                    async with s.get(url, timeout=aiohttp.ClientTimeout(total=5)) as r:
                        logger.info(f"✅ {url} 可访问 (Status: {r.status})")
                except Exception as e:
                    logger.warning(f"⚠️ {url} 不可访问 (非致命): {type(e).__name__}")

            await asyncio.gather(*[_check_url(url) for url in test_urls])

    async def setup(self):
        """Initialize all components"""
        # Ensure data directory exists
        os.makedirs(os.path.join(self.base_dir, "data"), exist_ok=True)
        
        # 0. Network Check
        await self.check_network()
        
        logger.info("Initializing components...")
        
        # 1. Initialize DBs
        await self.listener.init_db()
        await self.position_manager.init_manager()
        await self.performance_analyzer.init_db()
        
        if self.simulation_manager:
            await self.simulation_manager.init_db()
            logger.info("Simulation Manager initialized")
        
        # 2. Setup Web3 connections
        try:
            await self.listener.setup_web3()
        except Exception as e:
            logger.error(f"Listener Web3 setup failed: {e}")
            logger.warning("Continuing without Listener Web3 connection (some features may be limited)")
        
        # Initialize Executor
        logger.info("Initializing Executor...")
        try:
            await self.executor.init_executor()
        except Exception as e:
            logger.error(f"Executor initialization failed: {e}")
            logger.warning("Continuing without Executor Web3 connection (trading will be disabled)")
            
        # Executor web3 setup is done in init_executor
        if self.executor.w3 and await self.executor.w3.is_connected():
            logger.success("All components initialized successfully")
            
            # Pass Web3 to SecurityChecker for local simulation
            self.security_checker.set_web3(self.executor.w3)
            self.performance_analyzer.w3 = self.executor.w3
            logger.info("SecurityChecker & Analyzer updated with Web3 connection")
        else:
            logger.warning("Running in limited mode (Web3 disconnected)")

    async def run_scheduler(self):
        """Run periodic tasks (Daily/Weekly Reports)"""
        logger.info("Scheduler started...")
        while self.running:
            try:
                now = datetime.now()
                # Check if it's 8:00 AM (or shortly after) and we haven't reported today
                if now.hour == 8 and now.minute < 30: # 30 min window
                    today_str = now.strftime("%Y-%m-%d")
                    if self.last_report_date != today_str:
                        logger.info(f"Generating Daily Performance Report for {today_str}...")
                        
                        # 1. Update Stats
                        try:
                            # Run filter analysis first (to update verification_status for yesterday's pairs)
                            if self.performance_analyzer.w3:
                                await self.performance_analyzer.analyze_filter_effectiveness()
                                
                            # Then calculate daily stats (aggregating trades & filter stats)
                            await self.performance_analyzer.calculate_daily_stats()
                        except Exception as e:
                            logger.error(f"Stats calculation failed: {e}")

                        # 2. Generate & Send Daily Report
                        daily_report = await self.performance_analyzer.generate_daily_report_text()
                        await self.performance_analyzer.send_telegram_report(daily_report)
                        
                        # 3. Weekly Report (Monday)
                        if now.weekday() == 0: # Monday
                            logger.info("Generating Weekly Performance Report...")
                            weekly_report = await self.performance_analyzer.generate_weekly_report()
                            await self.performance_analyzer.send_telegram_report(weekly_report)
                            
                        # 4. Simulation Auto-Switch Analysis
                        if self.simulation_manager:
                            logger.info("Running simulation analysis...")
                            await self.simulation_manager.check_and_send_alerts()

                        self.last_report_date = today_str
                        logger.success(f"Daily report sent for {today_str}")
                
                # Check every minute
                await asyncio.sleep(60)
            except Exception as e:
                logger.error(f"Scheduler error: {e}")
                await asyncio.sleep(60)

    async def process_pairs(self):
        """Main pipeline: Consume pairs from listener queue"""
        logger.info("Starting pair processing pipeline...")
        
        while self.running:
            try:
                # Get new pair from queue
                pair_data = await self.listener.queue.get()
                # Spawn a background task for each pair to avoid blocking
                asyncio.create_task(self.process_single_pair(pair_data))
            except Exception as e:
                logger.error(f"Error in pair processing loop: {e}")

    async def process_single_pair(self, pair_data):
        """Process a single pair with parallel tasks and timing logs"""
        def _ms(t0): return (time.perf_counter() - t0) * 1000

        try:
            t_total = time.perf_counter()
            token_address = pair_data["token"]["address"]
            token_symbol = pair_data["token"]["symbol"]
            deployer = pair_data["deployer"]
            pair_address = pair_data["pair"]
            dex_name = pair_data["dex"]

            liquidity_bnb = pair_data.get("liquidity_bnb", 0.0)
            queue_size    = self.listener.queue.qsize()
            is_four_meme  = (dex_name == "four_meme")
            logger.info(
                f"Processing new pair: {token_symbol} ({token_address}) on {dex_name} "
                f"| liq={liquidity_bnb:.2f} BNB | queue_remaining={queue_size}"
            )

            # Check if paused
            if self.paused:
                logger.warning("Bot is paused. Skipping new pair.")
                return

            four_meme_cfg = self.config.get("four_meme", {})

            # ── 阶段1：初始状态获取 (four_meme 跳过，bonding curve 无 pair 地址) ──
            t1 = time.perf_counter()
            if is_four_meme:
                initial_state = {}
                logger.info(f"⏱️ 1.初始状态获取: 跳过(four_meme)")
                dur1 = 0
            else:
                try:
                    initial_state = await asyncio.wait_for(
                        self.security_checker.get_token_state(token_address, pair_address),
                        timeout=3.0
                    )
                except asyncio.TimeoutError:
                    logger.warning(f"⏱️ 1.初始状态获取超时(>3s)，跳过")
                    initial_state = {}
                dur1 = _ms(t1)
                logger.info(f"⏱️ 1.初始状态获取: {dur1:.0f}ms")

            # ── 阶段2：快速观察等待 (four_meme 时间窗口极短，直接跳过) ──
            t2 = time.perf_counter()
            skip_observation = is_four_meme and four_meme_cfg.get("skip_observation", True)
            quick_observe_time = 0 if skip_observation else self.config.get("monitor", {}).get("quick_observe_time", 5)
            if quick_observe_time > 0:
                logger.info(f"⏱️ 2.快速观察等待: {quick_observe_time * 1000:.0f}ms")
                await asyncio.sleep(quick_observe_time)
            else:
                logger.info(f"⏱️ 2.快速观察等待: 跳过({'four_meme' if skip_observation else 'quick_observe_time=0'})")

            # ── 阶段3+4：安全分析 + 预构建交易（并行）──
            t3 = time.perf_counter()

            async def run_security_check():
                ts = time.perf_counter()
                res = await self.security_checker.analyze(
                    token_address=token_address,
                    deployer_address=deployer,
                    pair_address=pair_address,
                    initial_state=initial_state,
                    platform='four_meme' if is_four_meme else None
                )
                logger.info(f"⏱️ 3.安全分析(内部): {_ms(ts):.0f}ms  score={res.get('final_score')} decision={res.get('decision')}")
                logger.info(f"    ├─ 分析耗时明细: analysis_time={res.get('analysis_time', 0)*1000:.0f}ms")
                return res

            async def run_pre_build():
                ts = time.perf_counter()
                result = await self.executor.pre_build_buy_tx(token_address)
                logger.info(f"⏱️ 4.预构建交易: {_ms(ts):.0f}ms  ok={result is not None and 'tx' in (result or {})}")
                return result

            security_task = asyncio.create_task(run_security_check())
            pre_build_task = asyncio.create_task(run_pre_build())

            analysis_result = await security_task
            pre_built_data = await pre_build_task
            dur34 = _ms(t3)   # ★ 立即捕获
            logger.info(f"⏱️ 3+4.并行(安全分析+预构建)合计: {dur34:.0f}ms")

            score = analysis_result["final_score"]
            decision = analysis_result["decision"]

            if is_four_meme:
                min_score = four_meme_cfg.get("min_score", 75)
            else:
                min_score = self.config.get("monitor", {}).get("min_security_score", 80)
            if score < min_score:
                logger.info(f"[{dex_name}] Security Score {score} < {min_score}. REJECT.")
                decision = "reject"

            logger.info(f"Score: {score} | Decision: {decision}")
            for risk in analysis_result["risk_items"]:
                logger.warning(f"Risk: {risk['desc']} ({risk['score']})")

            # ── 阶段5：DB 更新（后台非阻塞，不占买入关键路径）──
            import aiosqlite
            import json
            t5 = time.perf_counter()

            async def _update_pairs_db():
                try:
                    # timeout=1.0：本地写入超过 1s 说明 DB 被锁，立即放弃避免长等
                    async with aiosqlite.connect(self.db_path, timeout=1.0) as db:
                        risk_str = ",".join([r['desc'] for r in analysis_result["risk_items"]])
                        status = "bought" if decision in ["buy", "half_buy"] else "rejected"
                        if decision == "notify":
                            status = "analyzing"
                        await db.execute(
                            """UPDATE pairs SET
                               security_score = ?,
                               analysis_result = ?,
                               check_details = ?,
                               status = ?,
                               risk_reason = ?
                               WHERE pair_address = ?""",
                            (
                                score,
                                decision.upper(),
                                json.dumps(analysis_result["raw_data"]),
                                status,
                                risk_str,
                                pair_address,
                            ),
                        )
                        await db.commit()
                except Exception as e:
                    logger.error(f"Failed to update analysis result to DB: {e}")

            # 立即启动后台任务，不等它完成就继续买入
            db_write_task = asyncio.create_task(_update_pairs_db())
            dur5 = _ms(t5)   # ★ 立即捕获（应为 <1ms）
            logger.info(f"⏱️ 5.DB写入(后台启动): {dur5:.0f}ms")

            # ── 阶段6：买入执行（立即，不等 DB）──
            if decision in ["buy", "half_buy"]:
                t6 = time.perf_counter()

                # four_meme 使用专属 buy_amount，换算成 multiplier
                if is_four_meme:
                    base_amount = self.config.get("trading", {}).get("buy_amount", 0.1)
                    four_meme_amount = four_meme_cfg.get("buy_amount", 0.05)
                    amount_multiplier = (four_meme_amount / base_amount) if base_amount > 0 else 0.5
                    if decision == "half_buy":
                        amount_multiplier *= 0.5
                    logger.info(f"[four_meme] 买入金额: {four_meme_amount} BNB (multiplier={amount_multiplier:.2f})")
                    buy_result = await self.executor.buy_token(
                        token_address=token_address,
                        token_symbol=token_symbol,
                        amount_multiplier=amount_multiplier,
                        dex_name=dex_name
                    )
                elif pre_built_data and "tx" in pre_built_data:
                    logger.info("Using pre-built transaction for fast execution...")
                    pre_built_data["token_symbol"] = token_symbol
                    if decision == "half_buy":
                        pre_built_data["tx"]["value"] = pre_built_data["tx"]["value"] // 2
                    buy_result = await self.executor.fast_buy_token(pre_built_data)
                else:
                    logger.warning("Pre-build failed or missing, falling back to standard buy")
                    amount_multiplier = 0.5 if decision == "half_buy" else 1.0
                    buy_result = await self.executor.buy_token(
                        token_address=token_address,
                        token_symbol=token_symbol,
                        amount_multiplier=amount_multiplier
                    )
                # ★ 立即捕获耗时，之后不再用 _ms(t6)
                dur6 = _ms(t6)
                logger.info(f"⏱️ 6.买入执行: {dur6:.0f}ms  status={buy_result.get('status')}")

                if buy_result["status"] != "success":
                    reason = buy_result.get("reason", "unknown")
                    logger.warning(f"[{dex_name}] 买入失败: {reason}，更新DB状态")
                    async def _mark_buy_failed(pa=pair_address, r=reason):
                        try:
                            async with aiosqlite.connect(self.db_path, timeout=1.0) as db:
                                await db.execute(
                                    "UPDATE pairs SET status = 'buy_failed', risk_reason = ? WHERE pair_address = ?",
                                    (r, pa)
                                )
                                await db.commit()
                        except Exception as e:
                            logger.error(f"Failed to update buy_failed status: {e}")
                    asyncio.create_task(_mark_buy_failed())
                    return

                if buy_result["status"] == "success":
                    amount_bnb_in = buy_result.get("amount_bnb_in", 0.0)
                    token_amount  = buy_result.get("amount", 0.0)
                    buy_price     = amount_bnb_in / token_amount if token_amount > 0 else 0.0
                    buy_gas_price = buy_result.get("buy_gas_price", 0)

                    # ── 阶段7：DexScreener 后台发起，不阻塞仓位入库 ──
                    # DexScreener 失败不影响持仓逻辑，position_manager 监控循环会自动补全
                    asyncio.create_task(get_token_data(token_address))  # 真正后台
                    logger.info("⏱️ 7.DexScreener: 后台启动，不计入关键路径")

                    # ── 阶段8：仓位入库（立即，空 dex_data 可接受）──
                    t8 = time.perf_counter()
                    await self.position_manager.add_position(
                        token_address=token_address,
                        token_name=token_symbol,
                        buy_price=buy_price,
                        buy_amount_bnb=amount_bnb_in,
                        token_amount=token_amount,
                        buy_gas_price=buy_gas_price,
                        dex_data={},           # position_manager 监控会自动从 DexScreener 补全
                        pair_address=pair_address,
                        initial_liquidity_bnb=initial_state.get('liquidity_bnb', 0.0) if initial_state else 0.0,
                        dex_name=dex_name
                    )
                    dur8 = _ms(t8)
                    logger.info(f"⏱️ 8.仓位入库: {dur8:.0f}ms")

                    total_ms = _ms(t_total)
                    logger.success(
                        f"⏱️ ══ 买入成功全流程耗时汇总（关键路径）══\n"
                        f"    代币: {token_symbol} | 流动性: {liquidity_bnb:.2f} BNB\n"
                        f"    1.初始状态:      {dur1:.0f}ms\n"
                        f"    2.观察等待:      {quick_observe_time*1000:.0f}ms\n"
                        f"    3+4.分析+预构建: {dur34:.0f}ms\n"
                        f"    5.DB写入:        {dur5:.0f}ms (后台)\n"
                        f"    6.买入执行:      {dur6:.0f}ms  ← 关键\n"
                        f"    7.DexScreener:   后台\n"
                        f"    8.仓位入库:      {dur8:.0f}ms\n"
                        f"    ══ 关键路径总计: {total_ms:.0f}ms ══"
                    )
            else:
                logger.info(f"⏱️ 总耗时(被拒绝): {_ms(t_total):.0f}ms — {token_symbol} 未通过安全检测")

        except Exception as e:
            logger.error(f"Error processing pair {token_symbol if 'token_symbol' in locals() else 'Unknown'}: {e}")
            import traceback
            traceback.print_exc()
            logger.error(traceback.format_exc())

    async def stop(self):
        """Stop the bot gracefully"""
        logger.info("Stopping bot...")
        self.running = False
        self.listener.running = False
        self.position_manager.running = False
        
        # Cancel all tasks
        for task in self.tasks:
            task.cancel()
            
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
            
        logger.info("Bot stopped successfully")

    async def run_background(self):
        """Run bot in background tasks (for API)"""
        if self.running:
            logger.warning("Bot is already running")
            return

        self.running = True
        await self.setup()

        # Create tasks
        self.tasks = [
            asyncio.create_task(self.listener.run()),
            asyncio.create_task(self.position_manager.start_monitoring()),
            asyncio.create_task(self.process_pairs()),
            asyncio.create_task(self.run_scheduler())
        ]
        logger.success("Bot background tasks started")

    async def run(self):
        """Run the bot (CLI Mode)"""
        await self.run_background()
        
        # Keep running
        try:
            while self.running:
                await asyncio.sleep(1)
        except asyncio.CancelledError:
            logger.info("Bot stopping...")
        finally:
            await self.stop()
            
        await self.security_checker.close()
        await self.executor.close()
        logger.info("Bot stopped successfully")
