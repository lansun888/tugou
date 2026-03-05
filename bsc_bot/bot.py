import asyncio
import logging
import os
import sys
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
        try:
            start_time = asyncio.get_running_loop().time()
            token_address = pair_data["token"]["address"]
            token_symbol = pair_data["token"]["symbol"]
            deployer = pair_data["deployer"]
            pair_address = pair_data["pair"]
            dex_name = pair_data["dex"]
            
            logger.info(f"Processing new pair: {token_symbol} ({token_address}) on {dex_name}")
            
            # Check if paused
            if self.paused:
                logger.warning("Bot is paused. Skipping new pair.")
                return

            # pair_listener 已在入队前做过 observe_liquidity（等待 monitor.observation_wait_time 秒）
            # 这里只需做一次简短的二次观察（5秒）来捕获价格趋势变化，避免双重等待
            quick_observe_time = self.config.get("monitor", {}).get("quick_observe_time", 5)

            # Capture Initial State for Observation
            logger.info(f"Capturing initial state for {token_symbol}...")
            initial_state = await self.security_checker.get_token_state(token_address, pair_address)

            if quick_observe_time > 0:
                logger.info(f"Starting {quick_observe_time}s quick observation window...")
                await asyncio.sleep(quick_observe_time)

            logger.info(f"Observation finished. Starting parallel tasks (Analysis + Pre-build)...")
            
            # 1. Parallel Execution: Security Check + Transaction Pre-build
            async def run_security_check():
                t0 = asyncio.get_running_loop().time()
                res = await self.security_checker.analyze(
                    token_address=token_address,
                    deployer_address=deployer,
                    pair_address=pair_address,
                    initial_state=initial_state
                )
                logger.info(f"Security analysis took {asyncio.get_running_loop().time() - t0:.3f}s")
                return res
            
            async def run_pre_build():
                return await self.executor.pre_build_buy_tx(token_address)
            
            security_task = asyncio.create_task(run_security_check())
            pre_build_task = asyncio.create_task(run_pre_build())
            
            analysis_result = await security_task
            pre_built_data = await pre_build_task
            
            process_time = asyncio.get_running_loop().time() - start_time
            logger.info(f"Total Pipeline finished in {process_time:.3f}s")
            
            score = analysis_result["final_score"]
            decision = analysis_result["decision"]
            
            # Check against configured minimum security score
            min_score = self.config.get("monitor", {}).get("min_security_score", 80)
            if score < min_score:
                logger.info(f"Security Score {score} < {min_score}. REJECT.")
                decision = "reject"
            
            logger.info(f"Score: {score} | Decision: {decision}")
            for risk in analysis_result["risk_items"]:
                logger.warning(f"Risk: {risk['desc']} ({risk['score']})")

            # Update DB with analysis result
            try:
                import aiosqlite
                import json
                async with aiosqlite.connect(self.db_path) as db:
                    risk_str = ",".join([r['desc'] for r in analysis_result["risk_items"]])
                    status = "bought" if decision in ["buy", "half_buy"] else "rejected"
                    if decision == "notify": status = "analyzing" 

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
                            pair_address
                        )
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"Failed to update analysis result to DB: {e}")
            
            # 2. Auto Buy
            if decision in ["buy", "half_buy"]:
                buy_start = asyncio.get_running_loop().time()
                
                # Use Pre-built TX if available
                if pre_built_data and "tx" in pre_built_data:
                    logger.info("Using pre-built transaction for fast execution...")
                    
                    # Inject token symbol for better logging in executor
                    pre_built_data["token_symbol"] = token_symbol

                    # Handle half_buy logic
                    if decision == "half_buy":
                        original_value = pre_built_data["tx"]["value"]
                        pre_built_data["tx"]["value"] = original_value // 2
                        # Note: We don't update min_out here to be safe, relying on slippage tolerance or update it if needed
                        # But updating it requires recalculation. For speed, we assume slippage is fine or config is set loose.
                        
                    buy_result = await self.executor.fast_buy_token(pre_built_data)
                else:
                    logger.warning("Pre-build failed or missing, falling back to standard buy")
                    amount_multiplier = 0.5 if decision == "half_buy" else 1.0
                    buy_result = await self.executor.buy_token(
                        token_address=token_address,
                        token_symbol=token_symbol,
                        amount_multiplier=amount_multiplier
                    )
                
                if buy_result["status"] == "success":
                    total_time = asyncio.get_running_loop().time() - start_time
                    buy_time = asyncio.get_running_loop().time() - buy_start
                    logger.success(f"Buy Successful! Execution: {buy_time:.3f}s | Total Pipeline: {total_time:.3f}s")
                    
                    # 3. Add to Position Manager
                    amount_bnb_in = buy_result.get("amount_bnb_in", 0.0)
                    token_amount = buy_result.get("amount", 0.0)
                    buy_price = amount_bnb_in / token_amount if token_amount > 0 else 0.0
                    buy_gas_price = buy_result.get("buy_gas_price", 0)

                    # 3. Fetch DexScreener Data for Initial Stats
                    dex_data = {}
                    try:
                        logger.info(f"Fetching initial DexScreener data for {token_symbol}...")
                        dex_data = await get_token_data(token_address)
                        if dex_data:
                            logger.info(f"Initial Dex Data: MarketCap=${dex_data.get('market_cap', 0):.0f}, Vol=${dex_data.get('volume_24h', 0):.0f}")
                    except Exception as e:
                        logger.warning(f"Failed to fetch initial DexScreener data: {e}")

                    await self.position_manager.add_position(
                        token_address=token_address,
                        token_name=token_symbol,
                        buy_price=buy_price, 
                        buy_amount_bnb=amount_bnb_in, 
                        token_amount=token_amount,
                        buy_gas_price=buy_gas_price,
                        dex_data=dex_data,
                        pair_address=pair_address
                    )
            else:
                logger.info(f"Skipping {token_symbol} based on security check.")

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
