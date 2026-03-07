from fastapi import FastAPI, Depends, HTTPException, Header, Body, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
import asyncio
import hashlib
import logging
from typing import Optional, List, Dict, Any
import yaml
import json
import os
import signal
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi.responses import JSONResponse
from bsc_bot.bot import TradingBot
from web.database_helper import DatabaseHelper

# Configure logging
formatter = logging.Formatter("%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s")

file_handler = logging.FileHandler("bot.log", encoding='utf-8')
file_handler.setFormatter(formatter)

stream_handler = logging.StreamHandler()
stream_handler.setFormatter(formatter)

logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, stream_handler]
)
logger = logging.getLogger("web.api")

# Global bot instance
bot = None
db_helper = DatabaseHelper(db_path=os.path.abspath("d:/workSpace/tugou/bsc_bot/data/bsc_bot.db"))

class ConfigUpdate(BaseModel):
    config: Dict[str, Any]

class SellRequest(BaseModel):
    token_address: str
    percentage: float

class PositionParamsUpdate(BaseModel):
    stop_loss_price: Optional[float] = None
    target_price: Optional[float] = None
    buy_price_bnb: Optional[float] = None

async def verify_api_key(x_api_key: str = Header(...)):
    expected_key = "tugou_secret_key"
    if x_api_key != expected_key:
        # Allow checking env var too
        env_key = os.getenv("API_KEY")
        if env_key and x_api_key == env_key:
            return x_api_key
        raise HTTPException(status_code=403, detail="Invalid API Key")
    return x_api_key

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    logger.info("Initializing TradingBot... WITH_DEBUG_PRINTS_V2")
    
    # Attach file handler to uvicorn loggers to capture HTTP requests in bot.log
    # This ensures access logs are written to the file with our timestamp format
    for log_name in ["uvicorn", "uvicorn.access", "uvicorn.error"]:
        uvicorn_logger = logging.getLogger(log_name)
        # Check if file_handler is already in handlers (by instance)
        if not any(h is file_handler for h in uvicorn_logger.handlers):
            uvicorn_logger.addHandler(file_handler)
            
    global bot

    try:
        # Only create bot if not already injected from main.py
        # run_background() calls setup() internally, avoid double setup
        if bot is None:
            bot = TradingBot(mode='simulation')
            await bot.run_background()

    except Exception as e:
        logger.error(f"Failed to start bot: {e}")
        
    yield
    
    # Shutdown
    if bot:
        await bot.stop()

app = FastAPI(title="TuGou Bot API", lifespan=lifespan)

# CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/api/status", dependencies=[Depends(verify_api_key)])
async def get_status():
    try:
        if not bot:
            return {"status": "offline", "bnb_balance": 0, "active_positions": 0}
        
        stats = {
            "status": "running" if bot.running else "stopped",
            "paused": bot.paused,
            "mode": bot.mode,
            "bnb_balance": 0.0,
            "active_positions": 0,
            "today_profit_bnb": 0.0,
            "today_trades": 0,
            "win_rate": 0.0,
            "today_slippage_bnb": 0.0,
            "today_gas_bnb": 0.0,
            "today_tx_cost_bnb": 0.0
        }
        
        if bot.position_manager:
            stats["active_positions"] = len(bot.position_manager.positions)
            
        if bot.mode == 'simulation' and bot.simulation_manager:
            await bot.simulation_manager.recalculate_balance()
            stats['bnb_balance'] = bot.simulation_manager.balance_bnb
            stats['initial_balance'] = bot.simulation_manager.INITIAL_BALANCE_BNB
            
        # Get daily stats for today to fill profit and win rate
        daily = await db_helper.get_daily_stats(days=1, trades_table="simulation_trades" if bot.mode == "simulation" else "trades", positions_table="simulation_positions" if bot.mode == "simulation" else "positions")
        
        current_date = datetime.now().strftime("%Y-%m-%d")
        today_stats = None
        
        if daily:
            for d in daily:
                if d['day'] == current_date:
                    today_stats = d
                    break
        
        if today_stats:
            stats['today_profit_bnb'] = today_stats.get('total_pnl_bnb', 0.0)
            stats['today_buy_bnb'] = today_stats.get('total_buy_bnb', 0.0)
            stats['today_sell_bnb'] = today_stats.get('total_sell_bnb', 0.0)
            stats['today_slippage_bnb'] = today_stats.get('total_slippage_bnb', 0.0)
            stats['today_gas_bnb'] = today_stats.get('total_gas_cost_bnb', 0.0)
            stats['today_tx_cost_bnb'] = today_stats.get('total_tx_cost_bnb', 0.0)
            sell_count = today_stats.get('sell_count', 0)
            win_count = today_stats.get('win_count', 0)
            loss_count = today_stats.get('loss_count', 0)
            stats['today_trades'] = sell_count
            denominator = win_count + loss_count
            if denominator > 0:
                stats['win_rate'] = round((win_count / denominator) * 100, 1)

        # When in simulation mode, also compute live trading stats for comparison widget
        if bot.mode == 'simulation':
            live_daily = await db_helper.get_daily_stats(days=1, trades_table="trades", positions_table="positions")
            live_today = next((d for d in live_daily if d['day'] == current_date), None)
            if live_today:
                lw = live_today.get('win_count', 0)
                ll = live_today.get('loss_count', 0)
                ld = lw + ll
                stats['live_today_profit_bnb'] = live_today.get('total_pnl_bnb', 0.0)
                stats['live_today_trades'] = live_today.get('sell_count', 0)
                stats['live_win_rate'] = round((lw / ld) * 100, 1) if ld > 0 else 0.0
            else:
                stats['live_today_profit_bnb'] = 0.0
                stats['live_today_trades'] = 0
                stats['live_win_rate'] = 0.0

        return stats
    except Exception as e:
        logger.error(f"Error in get_status: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/positions", dependencies=[Depends(verify_api_key)])
async def get_positions():
    if bot is None or bot.position_manager is None:
        return []
    
    return _build_positions_list()

def _build_positions_list():
    if bot is None or bot.position_manager is None:
        return []

    # Return active positions from memory (exclude fully sold)
    positions = []
    try:
        for addr, pos in bot.position_manager.positions.items():
            if pos.status == "sold":
                continue
            # Calculate sold percentage
            sold_percent = 0.0
            if pos.sold_portions:
                for portion in pos.sold_portions:
                    if isinstance(portion, dict):
                        sold_percent += float(portion.get("percentage", 0.0))
            
            # Calculate current value if not set
            current_value = pos.current_value_bnb
            if current_value == 0 and pos.current_price > 0:
                current_value = pos.token_amount * pos.current_price

            # Calculate realized PnL
            realized_pnl = 0.0
            if pos.sold_portions:
                for portion in pos.sold_portions:
                    if isinstance(portion, dict):
                        realized_pnl += float(portion.get("pnl", 0.0))

            # Calculate unrealized PnL (Net PnL for remaining tokens)
            invested_bnb = pos.token_amount * pos.buy_price_bnb
            pnl_bnb = current_value - invested_bnb

            # Estimate stop loss and target price for display
            stop_loss_price = pos.buy_price_bnb * 0.5
            target_price = pos.buy_price_bnb * 2.0

            if bot.config:
                ts_cfg = bot.config.get("position_management", {}).get("trailing_stop", {})
                initial_sl = ts_cfg.get("initial_stop_loss", 50) # Default 50%
                
                # FIX: Stop Loss Logic (User Request: 1 - pct/100)
                # Ensure initial_sl is treated as a percentage DROP
                # If config has -50, abs(-50) = 50. If 50, abs(50) = 50.
                sl_pct = abs(float(initial_sl))
                # Guard against invalid config (>100%)
                if sl_pct >= 100:
                    logger.error(f"Configured Stop Loss % ({sl_pct}) is >= 100. Capping at 99%.")
                    sl_pct = 99.0
                
                stop_loss_price = pos.buy_price_bnb * (1 - sl_pct/100)
                
                # Double guard for negative price
                if stop_loss_price <= 0:
                    logger.error(f"Calculated negative Stop Loss: {stop_loss_price}. Resetting to 50% drop.")
                    stop_loss_price = pos.buy_price_bnb * 0.5

                # FIX: Target Price Logic (User Request: Multiplier)
                tp_cfg = bot.config.get("position_management", {}).get("take_profit", {})
                # Try to get multiplier from levels or default to 2x
                # If levels are [[100, 25], ...], first TP is +100% (2x)
                tp_levels = tp_cfg.get("levels", [])
                if tp_levels and len(tp_levels) > 0:
                    # Sort by percentage
                    sorted_levels = sorted(tp_levels, key=lambda x: x[0])
                    first_tp_pct = sorted_levels[0][0] # e.g. 100
                    # Target price = Buy Price * (1 + pct/100)
                    # e.g. 100% profit = 2x price
                    target_price = pos.buy_price_bnb * (1 + first_tp_pct/100)
                else:
                    # Fallback to hardcoded multiplier if no levels
                    target_price = pos.buy_price_bnb * 2.0

                # Validate: SL < Buy < TP
                if stop_loss_price >= pos.buy_price_bnb:
                    logger.error(f"Stop Loss Logic Error: SL ({stop_loss_price}) >= Buy ({pos.buy_price_bnb}). Fixing to 50%.")
                    stop_loss_price = pos.buy_price_bnb * 0.5
                
                if target_price <= pos.buy_price_bnb:
                    logger.error(f"Target Price Logic Error: TP ({target_price}) <= Buy ({pos.buy_price_bnb}). Fixing to 2x.")
                    target_price = pos.buy_price_bnb * 2.0

                # Pullback override (only if price went much higher)
                if pos.highest_price > pos.buy_price_bnb * 1.5:
                    pullback = ts_cfg.get("pullback_threshold", 40)
                    # High water mark stop
                    dynamic_sl = pos.highest_price * (1 - pullback/100)
                    stop_loss_price = max(stop_loss_price, dynamic_sl)

            # Manual overrides (set via PATCH /api/positions/{addr})
            if getattr(pos, 'manual_stop_loss', None) is not None:
                stop_loss_price = pos.manual_stop_loss
            if getattr(pos, 'manual_target_price', None) is not None:
                target_price = pos.manual_target_price

            # Guarantee target_price > stop_loss_price (trailing stop may have been raised above initial TP)
            if target_price <= stop_loss_price:
                target_price = stop_loss_price * 1.1

            positions.append({
                "token_address": pos.token_address,
                "token_symbol": pos.token_name, # Use name as symbol since position table doesn't store symbol
                "token_name": pos.token_name,
                "buy_price_bnb": pos.buy_price_bnb,
                "current_price_bnb": pos.current_price,
                "pnl_percentage": pos.pnl_percentage,
                "invested_bnb": invested_bnb,
                "current_value_bnb": current_value,
                "token_amount": pos.token_amount,
                "buy_time": pos.buy_time,
                "sold_percentage": sold_percent,
                "status": pos.status,
                "pnl_bnb": pnl_bnb,
                "realized_pnl_bnb": realized_pnl,
                "stop_loss_price": stop_loss_price,
                "target_price": target_price,
                "last_update_time": getattr(pos, "last_update_time", 0.0),
                "volume_24h": getattr(pos, "volume_24h", 0.0),
                "price_change_5m": getattr(pos, "price_change_5m", 0.0),
                "market_cap": getattr(pos, "market_cap", 0.0),
                "txns_5m_buys": getattr(pos, "txns_5m_buys", 0),
                "txns_5m_sells": getattr(pos, "txns_5m_sells", 0),
                "source": getattr(pos, "source", "unknown"),
                "dex_name": getattr(pos, "dex_name", None)
            })
    except Exception as e:
        logger.error(f"Error getting positions: {e}", exc_info=True)
        return []
        
    return positions

@app.post("/api/trade/sell", dependencies=[Depends(verify_api_key)])
async def sell_position(request: SellRequest):
    if not bot or not bot.position_manager:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    pos = bot.position_manager.positions.get(request.token_address)
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
        
    try:
        # Check if percentage is valid
        if request.percentage <= 0 or request.percentage > 100:
             raise HTTPException(status_code=400, detail="Invalid percentage (must be 0-100)")

        # Execute sell via PositionManager
        success = await bot.position_manager._execute_sell(pos, request.percentage, "manual_sell")
        
        if success:
            return {"status": "success", "message": f"Successfully sold {request.percentage}% of {pos.token_name}"}
        else:
             raise HTTPException(status_code=500, detail="Sell execution returned failure status")
             
    except Exception as e:
        logger.error(f"Manual sell failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

@app.patch("/api/positions/{token_address}", dependencies=[Depends(verify_api_key)])
async def update_position_params(token_address: str, update: PositionParamsUpdate):
    """手动设置止损/止盈价格"""
    if not bot or not bot.position_manager:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    pos = bot.position_manager.positions.get(token_address.lower())
    if not pos:
        raise HTTPException(status_code=404, detail="Position not found")
    if update.stop_loss_price is not None:
        if update.stop_loss_price <= 0:
            raise HTTPException(status_code=400, detail="stop_loss_price must be > 0")
        pos.manual_stop_loss = update.stop_loss_price
    if update.target_price is not None:
        if update.target_price <= 0:
            raise HTTPException(status_code=400, detail="target_price must be > 0")
        pos.manual_target_price = update.target_price
    if update.buy_price_bnb is not None:
        if update.buy_price_bnb <= 0:
            raise HTTPException(status_code=400, detail="buy_price_bnb must be > 0")
        pos.buy_price_bnb = update.buy_price_bnb
        # 同步更新 highest_price（防止 highest < buy）
        if pos.highest_price < pos.buy_price_bnb:
            pos.highest_price = pos.buy_price_bnb
        await bot.position_manager._save_position(pos)
    return {"status": "ok", "stop_loss_price": getattr(pos, 'manual_stop_loss', None), "target_price": getattr(pos, 'manual_target_price', None)}

@app.get("/api/simulation/stats", dependencies=[Depends(verify_api_key)])
async def get_simulation_stats(days: int = 7):
    if not bot or bot.mode != "simulation" or not bot.simulation_manager:
        return {"error": "Not in simulation mode"}
    
    try:
        # Retrieve stats from simulation manager or DB
        # We need to return the structure expected by the frontend
        
        trades_table = "simulation_trades"
        positions_table = "simulation_positions"
        
        daily_stats = await db_helper.get_daily_stats(days, trades_table, positions_table)
        
        # Aggregate stats
        stats = {
            "total_trades": 0,
            "win_count": 0,
            "loss_count": 0,
            "total_profit_bnb": 0.0,
            "expected_value": 0.0,
            "win_rate": 0.0,
            "avg_profit_bnb": 0.0,
            "avg_loss_bnb": 0.0
        }
        
        total_profit_sum = 0.0
        total_loss_sum = 0.0
        
        for day in daily_stats:
            stats["total_trades"] += day.get("sell_count", 0)
            stats["win_count"] += day.get("win_count", 0)
            stats["loss_count"] += day.get("loss_count", 0)
            stats["total_profit_bnb"] += day.get("total_pnl_bnb", 0.0)
            
            total_profit_sum += day.get("profit_bnb", 0.0)
            total_loss_sum += day.get("loss_bnb", 0.0)
            
        # Calculate derived stats
        denominator = stats["win_count"] + stats["loss_count"]
        if denominator > 0:
            stats["win_rate"] = round((stats["win_count"] / denominator) * 100, 2)
        if stats["total_trades"] > 0:
            stats["expected_value"] = round(stats["total_profit_bnb"] / stats["total_trades"], 4)
            
        if stats["win_count"] > 0:
            stats["avg_profit_bnb"] = round(total_profit_sum / stats["win_count"], 4)
            
        if stats["loss_count"] > 0:
            stats["avg_loss_bnb"] = round(total_loss_sum / stats["loss_count"], 4)
            
        # Generate analysis
        analysis = {
            "action": "observe",
            "message": "Collecting data...",
            "suggestions": []
        }
        
        if stats['total_trades'] > 5:
            if stats['win_rate'] > 60 and stats['total_profit_bnb'] > 0:
                analysis['action'] = "switch_to_live"
                analysis['message'] = "模拟表现优异，策略运行稳定。"
                analysis['suggestions'] = [
                    "可考虑以小仓位切换实盘验证",
                    "注意实盘滑点和 Gas 费用的影响"
                ]
            elif stats['total_profit_bnb'] < -0.1:
                analysis['action'] = "warning"
                analysis['message'] = "模拟出现亏损，请检查策略参数。"
                analysis['suggestions'] = [
                    "检查止损设置是否合理",
                    "排查入场条件是否过于宽松",
                    "分析亏损交易的共同规律"
                ]
            else:
                analysis['action'] = "observe"
                analysis['message'] = "策略运行平稳，表现一般，建议继续观察。"
                analysis['suggestions'] = [
                    "积累更多交易样本后再评估",
                    "分析盈亏混合的原因"
                ]
        else:
            analysis['message'] = f"数据不足（已完成 {stats['total_trades']}/5 笔交易），请等待更多样本。"
            analysis['suggestions'] = ["等待更多交易执行后再做分析"]

        return {
            "stats": stats,
            "analysis": analysis
        }
    except Exception as e:
        logger.error(f"Error fetching simulation stats: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})

@app.websocket("/ws/prices")
async def websocket_endpoint(websocket: WebSocket, api_key: str = Query(None)):
    # Verify key (handling absence or mismatch)
    expected_key = "tugou_secret_key"
    env_key = os.getenv("API_KEY")

    if api_key != expected_key and (not env_key or api_key != env_key):
        await websocket.close(code=1008, reason="Invalid API Key")
        return

    await websocket.accept()
    last_hash = None
    ticks_since_ping = 0  # Heartbeat counter (1 tick = 1s)
    HEARTBEAT_INTERVAL = 10  # Send ping every 10s when data unchanged

    try:
        while True:
            positions_data = _build_positions_list()

            # Compute hash to detect changes (avoids sending redundant full payloads)
            positions_str = json.dumps(positions_data, sort_keys=True, default=str)
            current_hash = hashlib.md5(positions_str.encode()).hexdigest()

            if current_hash != last_hash:
                # Data changed — send full update
                await websocket.send_json({
                    "type": "update",
                    "timestamp": datetime.now().isoformat(),
                    "positions": positions_data
                })
                last_hash = current_hash
                ticks_since_ping = 0
            else:
                # No change — send lightweight heartbeat every HEARTBEAT_INTERVAL seconds
                ticks_since_ping += 1
                if ticks_since_ping >= HEARTBEAT_INTERVAL:
                    await websocket.send_json({
                        "type": "ping",
                        "timestamp": datetime.now().isoformat()
                    })
                    ticks_since_ping = 0

            await asyncio.sleep(1)

    except WebSocketDisconnect:
        pass
    except Exception as e:
        logger.error(f"WebSocket error: {e}")
        try:
            await websocket.close()
        except:
            pass

@app.get("/api/trades", dependencies=[Depends(verify_api_key)])
async def get_trades(page: int = 1, limit: int = 20, type: str = "all"):
    table_name = "trades"
    positions_table = "positions"
    if bot and bot.mode == "simulation":
        table_name = "simulation_trades"
        positions_table = "simulation_positions"
    return await db_helper.get_trades(page, limit, type, table_name, positions_table)

@app.get("/api/discoveries", dependencies=[Depends(verify_api_key)])
async def get_discoveries(
    page: int = 1, 
    limit: int = 20, 
    result: str = "all", 
    search: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None
):
    return await db_helper.get_discoveries(page, limit, result, search, start_date, end_date)

@app.get("/api/stats/daily", dependencies=[Depends(verify_api_key)])
async def get_daily_stats(days: int = 7):
    trades_table = "trades"
    positions_table = "positions"
    if bot and bot.mode == "simulation":
        trades_table = "simulation_trades"
        positions_table = "simulation_positions"
        
    return await db_helper.get_daily_stats(days, trades_table, positions_table)

@app.get("/api/stats/hourly", dependencies=[Depends(verify_api_key)])
async def get_hourly_stats(hours: int = 24):
    trades_table = "trades"
    if bot and bot.mode == "simulation":
        trades_table = "simulation_trades"
    return await db_helper.get_hourly_stats(hours, trades_table)

@app.get("/api/config", dependencies=[Depends(verify_api_key)])
async def get_config():
    if not bot:
        return {}
    # Hide private key if present in config (though it should be in env)
    safe_config = bot.config.copy()
    return safe_config

def parse_log_line(line: str) -> Optional[Dict[str, Any]]:
    """Parse a log line into a structured object"""
    try:
        # Support multiple formats
        # Format 1 (Standard): "2024-03-03 15:37:38,123 | INFO     | module:func:123 - message"
        # Format 2 (Uvicorn): "INFO:     127.0.0.1:52603 - "GET /api/logs HTTP/1.1" 200 OK"
        
        if " | " in line:
            parts = line.split(" | ")
            if len(parts) >= 3:
                timestamp = parts[0].strip()
                level = parts[1].strip()
                remaining = parts[2]
                module_parts = remaining.split(" - ", 1)
                
                module_info = module_parts[0].strip() if len(module_parts) > 1 else "system"
                message = module_parts[1].strip() if len(module_parts) > 1 else remaining.strip()
                module_name = module_info.split(":")[0]
                
                return {
                    "timestamp": timestamp,
                    "level": level,
                    "module": module_name,
                    "message": message,
                    "raw": line.strip()
                }
        
        # Handle Uvicorn/FastAPI access logs or other formats
        if ":" in line:
            # Check if it starts with LEVEL:
            for level in ["INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"]:
                if line.startswith(f"{level}:"):
                    message = line[len(level)+1:].strip()
                    return {
                        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3], # Fallback timestamp
                        "level": level,
                        "module": "api.access",
                        "message": message,
                        "raw": line.strip()
                    }
                    
        # Fallback for unstructured lines
        return {
            "timestamp": "",
            "level": "INFO",
            "module": "system",
            "message": line.strip(),
            "raw": line.strip()
        }
    except Exception:
        return None

@app.get("/api/logs", dependencies=[Depends(verify_api_key)])
async def get_logs(limit: int = 100, level: str = "ALL"):
    # Try to find the log file
    log_files = ["bot.log", "logs/bot.log", "bsc_bot/logs/bot.log"]
    log_file = None
    
    for f in log_files:
        if os.path.exists(f):
            log_file = f
            break
            
    if not log_file:
        return {"logs": []}
        
    logs = []
    try:
        with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
            # Read all lines efficiently
            # For very large files, we might want to read from end, but 300KB is fine
            lines = f.readlines()
            
            # Process in reverse to get newest first
            count = 0
            for line in reversed(lines):
                if count >= limit:
                    break
                    
                parsed = parse_log_line(line)
                if parsed:
                    if level != "ALL" and parsed["level"] != level:
                        continue
                        
                    logs.append(parsed)
                    count += 1
                    
        return {"logs": logs}
    except Exception as e:
        logger.error(f"Error reading logs: {e}")
        return {"logs": [], "error": str(e)}

@app.post("/api/config", dependencies=[Depends(verify_api_key)])
async def update_config(update: ConfigUpdate):
    if not bot:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    try:
        with open(bot.config_path, "w", encoding="utf-8") as f:
            yaml.dump(update.config, f, allow_unicode=True)
        
        # Reload config
        new_config = bot.load_config()
        bot.config = new_config
        
        # Propagate to components
        if bot.executor:
            bot.executor.config = new_config
        if bot.listener:
            bot.listener.config = new_config
        if bot.position_manager:
            bot.position_manager.config = new_config.get("position_management", {})
        
        return {"status": "success", "message": "Config updated and reloaded."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/bot/start", dependencies=[Depends(verify_api_key)])
async def start_bot():
    if not bot:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    if bot.running:
        return {"status": "already_running"}
    
    await bot.run_background()
    return {"status": "started"}

@app.post("/api/bot/stop", dependencies=[Depends(verify_api_key)])
async def stop_bot():
    if not bot:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    await bot.stop()
    return {"status": "stopped"}

@app.post("/api/bot/pause", dependencies=[Depends(verify_api_key)])
async def pause_bot():
    if not bot:
        raise HTTPException(status_code=500, detail="Bot not initialized")
    
    bot.paused = not bot.paused # Toggle
    status = "paused" if bot.paused else "resumed"
    return {"status": status}

if __name__ == "__main__":
    # For debugging
    uvicorn.run("web.api:app", host="0.0.0.0", port=8002, reload=True)
