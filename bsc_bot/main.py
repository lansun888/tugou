import asyncio
import logging
import os
import sys
import signal
import argparse
from loguru import logger
from dotenv import load_dotenv
import yaml
import uvicorn

# Add current directory to python path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
# Add project root to python path for web module
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from monitor.pair_listener import PairListener
from analyzer.security_checker import SecurityChecker
from analyzer.performance import PerformanceAnalyzer
from executor.trader import BSCExecutor
from executor.position_manager import PositionManager
from simulation_manager import SimulationManager
from datetime import datetime
from bsc_bot.bot import TradingBot
import web.api # Import web api module

# Load environment variables
load_dotenv()

# Configure Logging
logger.remove()
log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs")
os.makedirs(log_dir, exist_ok=True)
log_file = os.path.join(log_dir, "bot.log")

logger.add(sys.stderr, level="INFO", format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>")
logger.add(log_file, rotation="10 MB", level="DEBUG", compression="zip", format="{time:YYYY-MM-DD HH:mm:ss} | {level: <8} | {name}:{function}:{line} - {message}")

async def main():
    parser = argparse.ArgumentParser(description='BSC Trading Bot')
    parser.add_argument('--mode', choices=['live', 'simulation'], help='Run mode: live or simulation', default=None)
    args = parser.parse_args()
    
    # Initialize Bot
    bot = TradingBot(mode=args.mode)
    await bot.setup()
    bot.running = True  # Enable processing loops before tasks start

    # Inject bot into Web API (Avoid double initialization)
    web.api.bot = bot
    logger.info("Bot instance injected into Web API")

    # Define Independent Tasks
    async def pair_listener():
        logger.info("Task: Pair Listener starting...")
        await bot.listener.run()

    async def price_monitor_loop():
        logger.info("Task: Price Monitor starting (Interval: 20s)...")
        # Ensure monitoring runs independently
        await bot.position_manager.start_monitoring()

    async def web_api_server():
        logger.info("Task: Web API Server starting (Port 8002)...")
        # Run uvicorn server programmatically
        # Disable signal handlers to let main loop handle exit signals
        config = uvicorn.Config(web.api.app, host="0.0.0.0", port=8002, log_level="info", loop="asyncio")
        server = uvicorn.Server(config)
        # Override install_signal_handlers
        server.install_signal_handlers = lambda: None
        await server.serve()
        
    async def processing_pipeline():
        logger.info("Task: Processing Pipeline starting...")
        await bot.process_pairs()
        
    async def scheduler_loop():
        logger.info("Task: Scheduler starting...")
        await bot.run_scheduler()

    # Create Tasks
    tasks = [
        asyncio.create_task(pair_listener()),      # 监听新币
        asyncio.create_task(price_monitor_loop()),  # 价格监控（这个是关键！）
        asyncio.create_task(web_api_server()),      # Web界面
        asyncio.create_task(processing_pipeline()), # 必须启动，否则队列堆积
        asyncio.create_task(scheduler_loop())       # 定时报告
    ]
    
    bot.tasks = tasks # Store tasks in bot for tracking if needed
    
    logger.info("所有任务已启动，价格监控每20秒运行一次")
    
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Main tasks cancelled")
    except Exception as e:
        logger.error(f"Main loop error: {e}")
    finally:
        logger.info("Shutting down...")
        await bot.stop()

if __name__ == "__main__":
    try:
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
