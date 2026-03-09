import aiosqlite
import logging
import json
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

class DatabaseHelper:
    def __init__(self, db_path="./data/bsc_bot.db"):
        self.db_path = db_path

    async def get_trades(self, page=1, limit=50, type="all", table_name="trades", positions_table="positions", time_range="all", token_search=""):
        offset = (page - 1) * limit
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(f"PRAGMA table_info({table_name})") as cursor:
                cols = await cursor.fetchall()
                columns = [col[1] for col in cols]
                if not columns:
                    return {"items": [], "total": 0, "page": page, "has_more": False}

            time_col = "timestamp" if "timestamp" in columns else "created_at" if "created_at" in columns else "time" if "time" in columns else "id"

            # 构建 WHERE 条件（服务端过滤）
            conditions = []
            filter_params = []

            if type != "all":
                conditions.append("LOWER(action) = ?")
                filter_params.append(type.lower())

            if time_range == "today":
                conditions.append(f"date({time_col}) = date('now')")
            elif time_range == "7d":
                conditions.append(f"{time_col} >= datetime('now', '-7 days')")
            elif time_range == "30d":
                conditions.append(f"{time_col} >= datetime('now', '-30 days')")

            if token_search and token_search.strip():
                s = f"%{token_search.strip()}%"
                search_cols = []
                for col in ("token_name", "token_symbol", "token_address"):
                    if col in columns:
                        search_cols.append(f"{col} LIKE ?")
                        filter_params.append(s)
                if search_cols:
                    conditions.append(f"({' OR '.join(search_cols)})")

            where_sql = ("WHERE " + " AND ".join(conditions)) if conditions else ""

            # COUNT 查询（索引覆盖，极快）
            async with db.execute(
                f"SELECT COUNT(*) FROM {table_name} {where_sql}", filter_params
            ) as count_cursor:
                count_row = await count_cursor.fetchone()
            total = count_row[0] if count_row else 0

            trade_query = f"SELECT * FROM {table_name} {where_sql} ORDER BY {time_col} DESC LIMIT ? OFFSET ?"
            params = filter_params + [limit, offset]

            # 先执行分页查询，再按实际出现的 token_address 精准查 positions（避免全表扫描）
            position_name_map = {}
            sold_records_map = {}
            buy_price_map = {}

            async with db.execute(trade_query, params) as trade_cursor:
                trade_rows = await trade_cursor.fetchall()

            # 收集当页涉及的 token_address
            page_token_addrs = list({
                row["token_address"] for row in trade_rows
                if row["token_address"]
            })

            if page_token_addrs:
                async with db.execute(f"PRAGMA table_info({positions_table})") as pos_cursor:
                    pos_columns = [col[1] for col in await pos_cursor.fetchall()]

                if pos_columns:
                    placeholders = ",".join("?" * len(page_token_addrs))
                    async with db.execute(
                        f"SELECT token_address, token_name, buy_price_bnb, sold_portions FROM {positions_table} WHERE token_address IN ({placeholders})",
                        page_token_addrs
                    ) as pos_data:
                        for row in await pos_data.fetchall():
                            token_address = row["token_address"]
                            if token_address:
                                key = token_address.lower()
                                position_name_map[key] = row["token_name"]
                                buy_price_map[key] = row["buy_price_bnb"]
                            sold_portions = row["sold_portions"]
                            if sold_portions and token_address:
                                key = token_address.lower()
                                try:
                                    parsed = json.loads(sold_portions)
                                    sold_records_map[key] = [{"used": False, **r} for r in parsed]
                                except Exception:
                                    sold_records_map[key] = []

            # 复用 trade_rows 作为后续处理的数据源
            rows = trade_rows

            def to_float(value):
                try:
                    if value is None:
                        return 0.0
                    return float(value)
                except Exception:
                    return 0.0

            def parse_timestamp(value):
                if value is None:
                    return 0
                if isinstance(value, (int, float)):
                    v = float(value)
                    if v > 1e12:
                        return v / 1000
                    if v > 1e10:
                        return v / 1000
                    return v
                try:
                    value_str = str(value)
                    if value_str.isdigit():
                        v = float(value_str)
                        if v > 1e12:
                            return v / 1000
                        if v > 1e10:
                            return v / 1000
                        return v
                    return datetime.fromisoformat(value_str.replace("T", " ").replace("Z", "")).timestamp()
                except Exception:
                    return 0

            def normalize_reason(reason):
                if not reason:
                    return None
                r = str(reason)
                if r.startswith("tp_"):
                    return "take_profit"
                if r.startswith("time_stop"):
                    return "time_stop"
                if "stop" in r or "trailing" in r or "pullback" in r:
                    return "stop_loss"
                if "rug" in r:
                    return "rug"
                if "manual" in r:
                    return "manual"
                return r

            normalized = []
            for row in rows:
                item = dict(row)
                token_address = item.get("token_address")
                token_name = item.get("token_name")
                if token_address and not token_name:
                    token_name = position_name_map.get(token_address.lower())
                    
                token_symbol = item.get("token_symbol")
                if not token_symbol:
                    token_symbol = token_name
                if not token_name:
                    token_name = token_symbol

                action = (item.get("action") or "").lower()
                amount_token = to_float(item.get("amount_token", item.get("amount")))
                amount_bnb = to_float(item.get("amount_bnb"))
                price_bnb = to_float(item.get("price_bnb", item.get("price")))
                    
                # Fix for potential bad data (0.001 price placeholder)
                if price_bnb == 0.001:
                    price_bnb = 0.0

                if amount_bnb <= 0 and amount_token > 0 and price_bnb > 0:
                    amount_bnb = amount_token * price_bnb
                if price_bnb <= 0 and amount_token > 0 and amount_bnb > 0:
                    price_bnb = amount_bnb / amount_token
                created_at = item.get("created_at", item.get("timestamp", item.get("time")))
                ts = parse_timestamp(created_at)
                if isinstance(created_at, (int, float)) or (isinstance(created_at, str) and created_at.isdigit()):
                    created_at = datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts > 0 else created_at

                trade_type = "simulation" if table_name.startswith("simulation") else "live"
                pnl_bnb = to_float(item.get("pnl_bnb"))
                pnl_percentage = to_float(item.get("pnl_percentage"))
                close_reason = None

                if action == "sell" and token_address and token_address.lower() in sold_records_map:
                    records = sold_records_map[token_address.lower()]
                    best_index = None
                    best_diff = None
                    for idx, record in enumerate(records):
                        if record.get("used"):
                            continue
                        record_time = parse_timestamp(record.get("time"))
                        diff = abs(record_time - ts) if ts > 0 and record_time > 0 else None
                        record_amount = to_float(record.get("amount"))
                        if diff is None:
                            continue
                        if amount_token > 0 and record_amount > 0:
                            ratio = abs(record_amount - amount_token) / max(record_amount, amount_token)
                            if ratio > 0.6:
                                continue
                        if best_diff is None or diff < best_diff:
                            best_diff = diff
                            best_index = idx
                    if best_index is not None and best_diff is not None and best_diff < 6 * 3600:
                        record = records[best_index]
                        record["used"] = True
                        close_reason = normalize_reason(record.get("reason"))
                            
                        # Fix amount_bnb and price_bnb from sold record if missing or suspicious
                        record_bnb_got = to_float(record.get("bnb_got"))
                        if record_bnb_got > 0:
                            # Update if amount_bnb is missing/zero or looks like it was calculated from 0.001 price
                            # Or if price is 0 (we just set 0.001 to 0 above)
                            if amount_bnb <= 0 or price_bnb == 0 or (amount_bnb > 10 and amount_token > 1000):
                                 amount_bnb = record_bnb_got
                                 if amount_token > 0:
                                     price_bnb = amount_bnb / amount_token

                        if pnl_bnb == 0 and record.get("pnl") is not None:
                            pnl_bnb = to_float(record.get("pnl"))
                        if pnl_percentage == 0 and pnl_bnb != 0:
                            record_amount = to_float(record.get("amount"))
                            buy_price = to_float(buy_price_map.get(token_address.lower()))
                            cost = record_amount * buy_price if record_amount > 0 and buy_price > 0 else 0.0
                            if cost > 0:
                                pnl_percentage = pnl_bnb / cost * 100

                normalized.append({
                    "id": item.get("id"),
                    "token_address": token_address,
                    "token_name": token_name,
                    "token_symbol": token_symbol,
                    "action": action,
                    "amount_token": amount_token,
                    "amount_bnb": amount_bnb,
                    "price_bnb": price_bnb,
                    "pnl_bnb": pnl_bnb,
                    "pnl_percentage": pnl_percentage,
                    "tx_hash": item.get("tx_hash"),
                    "created_at": created_at,
                    "trade_type": trade_type,
                    "close_reason": close_reason,
                    "status": item.get("status"),
                    "expected_amount": to_float(item.get("expected_amount")),
                    "actual_amount": to_float(item.get("actual_amount")),
                    "slippage_pct": to_float(item.get("slippage_pct")),
                    "slippage_bnb": to_float(item.get("slippage_bnb")),
                    "gas_used": int(item.get("gas_used") or 0),
                    "gas_price_gwei": to_float(item.get("gas_price_gwei")),
                    "gas_cost_bnb": to_float(item.get("gas_cost_bnb")),
                    "total_cost_bnb": to_float(item.get("total_cost_bnb")),
                    "dex_name": item.get("dex_name"),
                    "_ts": ts
                })

            normalized.sort(key=lambda x: x["_ts"])
            lots_map = {}
            for trade in normalized:
                token_address = trade["token_address"]
                if not token_address:
                    continue
                lots = lots_map.setdefault(token_address, [])
                if trade["action"] == "buy":
                    if trade["amount_token"] > 0 and trade["price_bnb"] > 0:
                        lots.append({"amount": trade["amount_token"], "price": trade["price_bnb"]})
                    continue
                if trade["action"] == "sell" and (trade["pnl_bnb"] == 0 or trade["pnl_percentage"] == 0):
                    sell_price = trade["price_bnb"]
                    sell_amount = trade["amount_token"]
                    if sell_price > 0 and sell_amount > 0 and lots:
                        remaining = sell_amount
                        cost_sum = 0.0
                        sell_sum = 0.0
                        while remaining > 0 and lots:
                            lot = lots[0]
                            lot_amount = lot["amount"]
                            use_amount = min(remaining, lot_amount)
                            cost_sum += use_amount * lot["price"]
                            sell_sum += use_amount * sell_price
                            lot["amount"] -= use_amount
                            remaining -= use_amount
                            if lot["amount"] <= 0:
                                lots.pop(0)
                        if cost_sum > 0:
                            pnl = sell_sum - cost_sum
                            trade["pnl_bnb"] = pnl if trade["pnl_bnb"] == 0 else trade["pnl_bnb"]
                            if trade["pnl_percentage"] == 0:
                                trade["pnl_percentage"] = pnl / cost_sum * 100

            normalized.sort(key=lambda x: x["_ts"], reverse=True)
            for item in normalized:
                item.pop("_ts", None)
            return {
                "items": normalized,
                "total": total,
                "page": page,
                "has_more": (offset + len(normalized)) < total
            }

    async def get_discoveries(self, page=1, limit=20, result="all", search=None, start_date=None, end_date=None):
        offset = (page - 1) * limit
        query = "SELECT * FROM pairs"
        params = []
        conditions = []
        
        if result != "all":
            if result == "bought":
                conditions.append("status = 'bought'")
            elif result == "rejected":
                conditions.append("status = 'rejected'")
            elif result == "analyzing":
                conditions.append("status = 'analyzing'")
        
        if search:
            conditions.append("(token_name LIKE ? OR token_symbol LIKE ? OR target_token LIKE ?)")
            search_term = f"%{search}%"
            params.extend([search_term, search_term, search_term])
            
        if start_date:
            conditions.append("discovered_at >= ?")
            params.append(start_date)
            
        if end_date:
            conditions.append("discovered_at <= ?")
            params.append(end_date)
            
        if conditions:
            query += " WHERE " + " AND ".join(conditions)
            
        query += " ORDER BY discovered_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, params) as cursor:
                rows = await cursor.fetchall()
                results = []
                for row in rows:
                    item = dict(row)
                    
                    # Map fields for frontend
                    item['token_address'] = item['target_token']
                    item['discovery_time'] = item['discovered_at']
                    item['result'] = item.get('status', 'analyzing')
                    
                    # Parse JSON/Complex fields
                    try:
                        # If check_details is stored as JSON string
                        if item.get('check_details') and isinstance(item['check_details'], str):
                            item['check_details'] = json.loads(item['check_details'])
                        else:
                            item['check_details'] = {}
                    except:
                        item['check_details'] = {}

                    # Risk factors
                    if item.get('risk_reason'):
                        item['risk_factors'] = item['risk_reason'].split(',')
                    else:
                        item['risk_factors'] = []

                    item['is_delayed_honeypot'] = False
                    results.append(item)

                # Detect delayed honeypot: check sold_portions in positions tables
                bought_items = [r for r in results if r.get('status') == 'bought']
                if bought_items:
                    token_addrs = [r['target_token'] for r in bought_items if r.get('target_token')]
                    delayed_hp_set = set()
                    for tbl in ('simulation_positions', 'positions'):
                        try:
                            placeholders = ','.join(['?'] * len(token_addrs))
                            async with db.execute(
                                f"SELECT token_address, sold_portions FROM {tbl} WHERE token_address IN ({placeholders})",
                                token_addrs
                            ) as sp_cursor:
                                for sp_row in await sp_cursor.fetchall():
                                    addr, sold_json = sp_row[0], sp_row[1]
                                    if not sold_json:
                                        continue
                                    try:
                                        records = json.loads(sold_json)
                                        for rec in records:
                                            if rec.get('reason') in ('liq_drain_2min', 'no_momentum_2min'):
                                                delayed_hp_set.add(addr.lower())
                                                break
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                    for r in results:
                        if r.get('target_token', '').lower() in delayed_hp_set:
                            r['is_delayed_honeypot'] = True

                return results

    async def update_pair_analysis(self, pair_address, score, analysis_result, check_details, status, risk_factors):
        """Update pair with analysis results"""
        # Convert risk_factors (list of dicts or strings) to string for DB
        risk_str = ""
        if risk_factors:
            risk_str = ",".join([r['desc'] if isinstance(r, dict) else str(r) for r in risk_factors])
            
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """UPDATE pairs SET 
                   security_score = ?, 
                   analysis_result = ?, 
                   check_details = ?, 
                   status = ?,
                   risk_reason = ?
                   WHERE pair_address = ?""",
                (score, analysis_result, json.dumps(check_details), status, risk_str, pair_address)
            )
            await db.commit()

    async def get_daily_stats(self, days=7, trades_table="trades", positions_table="positions"):
        # 1. Get trade counts from trades table
        start_date = (datetime.now() - timedelta(days=days)).strftime("%Y-%m-%d")

        # Check if the trades table exists; return empty list if not
        try:
            async with aiosqlite.connect(self.db_path) as _db:
                async with _db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (trades_table,)
                ) as _cur:
                    if not await _cur.fetchone():
                        return []
        except Exception:
            return []

        # Determine time column dynamically from actual table schema
        time_col = 'created_at'
        try:
            async with aiosqlite.connect(self.db_path) as _db:
                async with _db.execute(f"PRAGMA table_info({trades_table})") as _cur:
                    _cols = [r[1] for r in await _cur.fetchall()]
                    if 'created_at' in _cols:
                        time_col = 'created_at'
                    elif 'timestamp' in _cols:
                        time_col = 'timestamp'
                    elif 'time' in _cols:
                        time_col = 'time'
        except Exception:
            pass

        # FIX: Use user's requested Cash Flow PnL logic (Total Sell - Total Buy)
        # We calculate this directly from trades table.
        # Note: We group by day.
        # FIX 2: Win Rate Logic Update (Coin-based)
        # We need to calculate win/loss based on TOKEN outcomes per day, not individual trades.
        # A coin is a "Win" if net_bnb > 0 AND has_sell=1.
        # A coin is a "Loss" if net_bnb <= 0 AND has_sell=1.
        
        # We use a CTE to aggregate per coin per day first.
        # Note: SQLite CTE support is standard.
        query = f"""
            WITH coin_daily_pnl AS (
                SELECT 
                    date({time_col}) as day,
                    token_address,
                    SUM(CASE WHEN action='buy' THEN -amount_bnb ELSE amount_bnb END) as net_bnb,
                    MAX(CASE WHEN action='sell' AND status='success' THEN 1 ELSE 0 END) as has_sell
                FROM {trades_table}
                WHERE {time_col} >= ?
                GROUP BY day, token_address
            ),
            daily_coin_stats AS (
                SELECT
                    day,
                    SUM(CASE WHEN net_bnb > 0 AND has_sell=1 THEN 1 ELSE 0 END) as win_coins,
                    SUM(CASE WHEN net_bnb <= 0 AND has_sell=1 THEN 1 ELSE 0 END) as lose_coins
                FROM coin_daily_pnl
                GROUP BY day
            )
            SELECT 
                date(t.{time_col}) as day,
                COUNT(*) as total_trades,
                SUM(CASE WHEN action='sell' AND status='success' THEN 1 ELSE 0 END) as sell_count,
                SUM(CASE WHEN action='buy' AND status='success' THEN 1 ELSE 0 END) as buy_count,
                -- Old trade-based win/loss (kept for reference or backward compat if needed, but we overwrite below)
                SUM(CASE WHEN action='sell' AND status='success' AND pnl_bnb > 0 THEN 1 ELSE 0 END) as trade_win_count,
                SUM(CASE WHEN action='sell' AND status='success' AND pnl_bnb <= 0 THEN 1 ELSE 0 END) as trade_loss_count,
                -- 已实现PnL：只累加 sell 记录的 pnl_bnb（含 failed_rug 的 -0.1，不含 phantom buy）
                SUM(CASE WHEN action='sell' THEN CAST(pnl_bnb AS REAL) ELSE 0 END) as total_pnl_bnb,
                SUM(CASE WHEN action='sell' AND CAST(pnl_bnb AS REAL) > 0 THEN CAST(pnl_bnb AS REAL) ELSE 0 END) as profit_bnb,
                SUM(CASE WHEN action='sell' AND CAST(pnl_bnb AS REAL) < 0 THEN ABS(CAST(pnl_bnb AS REAL)) ELSE 0 END) as loss_bnb,
                SUM(CASE WHEN action='sell' AND status='success' THEN amount_bnb ELSE 0 END) as total_sell_bnb,
                SUM(CASE WHEN action='buy' AND status='success' THEN amount_bnb ELSE 0 END) as total_buy_bnb,
                SUM(slippage_bnb) as total_slippage_bnb,
                SUM(gas_cost_bnb) as total_gas_cost_bnb,
                SUM(total_cost_bnb) as total_tx_cost_bnb,
                -- Join with coin stats
                COALESCE(dcs.win_coins, 0) as win_count,
                COALESCE(dcs.lose_coins, 0) as loss_count
            FROM {trades_table} t
            LEFT JOIN daily_coin_stats dcs ON dcs.day = date(t.{time_col})
            WHERE t.{time_col} >= ?
            GROUP BY day
            ORDER BY day ASC
        """
        
        stats_map = {}
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            
            # Fetch trade counts
            # Need to pass start_date twice (for CTE and main query)
            async with db.execute(query, (start_date, start_date)) as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    day = row['day']
                    stats_map[day] = dict(row)
                    # Initialize default values if not present (though SQL handles this)
                    if 'profit_bnb' not in stats_map[day]: stats_map[day]['profit_bnb'] = 0.0
                    if 'loss_bnb' not in stats_map[day]: stats_map[day]['loss_bnb'] = 0.0
                    if 'total_sell_bnb' not in stats_map[day]: stats_map[day]['total_sell_bnb'] = 0.0
                    if 'total_buy_bnb' not in stats_map[day]: stats_map[day]['total_buy_bnb'] = 0.0
            
            # Track which dates have data from trades table
            dates_from_trades_table = set(stats_map.keys())

            # 1b. Count buy_count from positions table (buy_time per day)
            # In simulation mode, IF buys are not written to trades table, we read from positions
            # BUT user requested strict Cash Flow from trades table. 
            # If trades table is used, we should trust it.
            # However, existing logic adds buy_count from positions. We keep this for counts, but NOT for PnL.
            buy_count_query = f"""
                SELECT date(datetime(buy_time, 'unixepoch')) as day, COUNT(*) as cnt
                FROM {positions_table}
                WHERE buy_time >= ?
                GROUP BY day
            """
            start_ts = (datetime.now() - timedelta(days=days)).timestamp()
            async with db.execute(buy_count_query, (start_ts,)) as cursor:
                for row in await cursor.fetchall():
                    day = row[0]
                    cnt = row[1]
                    if day:
                        if day not in stats_map:
                            stats_map[day] = {
                                "day": day, "total_trades": 0, "sell_count": 0,
                                "buy_count": 0, "win_count": 0, "loss_count": 0,
                                "profit_bnb": 0.0, "loss_bnb": 0.0, "total_pnl_bnb": 0.0
                            }
                        # Overwrite buy_count: positions table is authoritative for buys count if trades table misses them
                        # But for PnL we rely on trades table as per user request
                        if day not in dates_from_trades_table:
                             stats_map[day]['buy_count'] = cnt

            # 2. Calculate PnL from positions table (sold_portions) - DISABLED FOR PNL
            # User requested: "不要加任何持仓浮盈" (Do not add any floating PnL) and "只计算已完成交易" (Only completed trades).
            # The user's SQL implies using TRADES table for PnL.
            # The existing logic below iterates positions and ADDS Realized PnL to the stats.
            # This causes double counting or mixing of Cash Flow and Realized PnL.
            # We will ONLY use this to update counts if the date was missing from trades table,
            # BUT we will NOT update total_pnl_bnb if we already have it from trades table.
            
            # Actually, if we want to be safe and strictly follow "Total Sell - Total Buy",
            # we should NOT add anything from positions table to PnL.
            # Because positions table logic here calculates Realized PnL (Sell - Cost), not Cash Flow.
            
            # We keep the loop to backfill dates that might be missing in trades table (e.g. if trades table is empty),
            # but we need to be careful about PnL.
            # If trades table is empty, we have 0 PnL.
            # If we rely on positions table, we get Realized PnL.
            # If user wants Cash Flow, Realized PnL is "better than nothing" but not strict Cash Flow.
            # However, user explicitly provided SQL for TRADES table.
            # So we assume TRADES table is the source of truth for PnL.
            
            # We will comment out PnL update from positions table to strictly follow user request.
            pass
            
            # (Original logic for reference - now skipped for PnL)
            # async with db.execute(f"SELECT buy_price_bnb, sold_portions FROM {positions_table} WHERE sold_portions IS NOT NULL AND sold_portions != '[]'") as cursor:
            #    ... (omitted) ...
        
        # Fill missing days with zero so charts always have a full range
        for i in range(days):
            day = (datetime.now() - timedelta(days=i)).strftime("%Y-%m-%d")
            if day not in stats_map:
                stats_map[day] = {
                    "day": day, "total_trades": 0, "sell_count": 0, "buy_count": 0,
                    "win_count": 0, "loss_count": 0, "profit_bnb": 0.0,
                    "loss_bnb": 0.0, "total_pnl_bnb": 0.0
                }

        # Convert map to sorted list
        result = sorted(stats_map.values(), key=lambda x: x['day'])
        return result

    async def get_hourly_stats(self, hours=24, trades_table="trades"):
        start_time = (datetime.now() - timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

        # Check if the trades table exists; return empty list if not
        try:
            async with aiosqlite.connect(self.db_path) as _db:
                async with _db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (trades_table,)
                ) as _cur:
                    if not await _cur.fetchone():
                        return []
        except Exception:
            return []

        time_col = 'created_at'
        try:
            async with aiosqlite.connect(self.db_path) as _db:
                async with _db.execute(f"PRAGMA table_info({trades_table})") as _cur:
                    _cols = [r[1] for r in await _cur.fetchall()]
                    if 'created_at' in _cols:
                        time_col = 'created_at'
                    elif 'timestamp' in _cols:
                        time_col = 'timestamp'
                    elif 'time' in _cols:
                        time_col = 'time'
        except Exception:
            pass
        
        query = f"""
            SELECT 
                strftime('%Y-%m-%d %H:00:00', {time_col}) as hour,
                COUNT(*) as total_trades
            FROM {trades_table}
            WHERE {time_col} >= ?
            GROUP BY hour
            ORDER BY hour ASC
        """
        
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(query, (start_time,)) as cursor:
                rows = await cursor.fetchall()
                return [dict(row) for row in rows]
