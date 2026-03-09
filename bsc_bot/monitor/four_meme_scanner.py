"""
FourMemeRealtimeScanner
=======================
Real-time monitoring of four.meme bonding curve tokens via BSC chain WebSocket.
Detects tokens approaching graduation (75-92% funded) and triggers buy evaluation.

Flow:
  1. 链上 WebSocket 订阅 Transfer 事件（from=Factory），零成本实时信号
  2. _on_token_activity() reads bonding curve progress via 0xe684626b factory call
  3. At progress_min..progress_max, evaluate security and buy
  4. Add position with pregrad_buy=True for special monitoring in position_manager
"""

import asyncio
import json
import time

import aiosqlite
import websockets
from loguru import logger

from web3 import AsyncWeb3

FACTORY_ADDRESS    = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
FACTORY_ADDRESS_LC = FACTORY_ADDRESS.lower()
FACTORY_SEL        = "e684626b"

# Transfer(address indexed from, address indexed to, uint256 value)
TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
# Factory address 左填充到 32 字节（作为 Transfer.from 过滤）
FACTORY_PADDED = "0x000000000000000000000000" + FACTORY_ADDRESS[2:].lower()

# 备用免费公共 WSS 节点（按优先级）
FALLBACK_WSS_NODES = [
    "wss://bsc-rpc.publicnode.com",
    "wss://1rpc.io/bnb",
]

FOUR_MEME_WATCHLIST_DDL = """
CREATE TABLE IF NOT EXISTS four_meme_watchlist (
    token_address         TEXT PRIMARY KEY,
    token_name            TEXT DEFAULT '',
    token_symbol          TEXT DEFAULT '',
    created_at            TIMESTAMP DEFAULT (datetime('now')),
    progress              REAL DEFAULT 0,
    last_activity         TIMESTAMP,
    evaluated_at_progress REAL DEFAULT 0,
    status                TEXT DEFAULT 'watching',
    score                 INTEGER DEFAULT 0,
    reject_reason         TEXT DEFAULT ''
)
"""


class FourMemeRealtimeScanner:
    """
    Real-time scanner for four.meme bonding curve tokens.
    Uses Bitquery WebSocket as primary signal source, with DB watchlist polling as fallback.
    When a token reaches progress_min..progress_max, evaluates and potentially buys.
    """

    def __init__(self, w3, db_path: str, security_checker, executor, position_manager, config: dict):
        self.w3 = w3
        self.db_path = db_path
        self.security_checker = security_checker
        self.executor = executor
        self.position_manager = position_manager

        scanner_cfg = config.get("four_meme_scanner", {})
        self.progress_min = float(scanner_cfg.get("progress_min", 75))
        self.progress_max = float(scanner_cfg.get("progress_max", 92))
        self.buy_amount   = float(scanner_cfg.get("buy_amount_bnb", 0.05))
        self.min_score    = int(scanner_cfg.get("min_score", 75))
        self.max_wait_min = float(scanner_cfg.get("pregrad_max_wait_minutes", 20))
        self.regress_pct  = float(scanner_cfg.get("pregrad_regress_pct", 5))

        # WebSocket 节点列表（config ws_node 优先，后接公共免费节点）
        primary_wss = config.get("network", {}).get("ws_node", "")
        self._wss_nodes = ([primary_wss] if primary_wss else []) + FALLBACK_WSS_NODES
        self._ws_url_index = 0

        self._processing: set = set()   # token_address currently being processed
        self._evaluated: set  = set()   # already triggered buy evaluation
        self._running: bool   = False

    # ─────────────────────────────────────────────────────────────
    # Entry Point
    # ─────────────────────────────────────────────────────────────

    async def run(self):
        logger.info("FourMemeRealtimeScanner 启动")
        self._running = True
        await self._init_watchlist_table()

        await asyncio.gather(
            self._chain_websocket_stream(),   # 链上实时流（BSC WebSocket）
            self._chain_polling_fallback(),   # 热门代币 10s 轮询（补漏）
            self._cold_token_polling(),       # 冷门代币 2min 轮询
            self._cleanup_dead_tokens(),      # 死币清理 10min
        )

    # ─────────────────────────────────────────────────────────────
    # DB Init
    # ─────────────────────────────────────────────────────────────

    async def _init_watchlist_table(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(FOUR_MEME_WATCHLIST_DDL)
            await db.commit()
        logger.info("four_meme_watchlist 表已就绪")

    # ─────────────────────────────────────────────────────────────
    # Primary: BSC Chain WebSocket Stream（直连链上，完全免费）
    # ─────────────────────────────────────────────────────────────

    async def _chain_websocket_stream(self):
        """
        用 websockets 原始 JSON-RPC eth_subscribe 订阅 BSC 链上 Transfer 事件（from=Factory）。
        避免 web3.py subscribe 封装的兼容性问题，完全免费。
        """
        logger.info("链上WebSocket流 启动...")
        retry_count = 0

        while self._running:
            wss_url = self._wss_nodes[self._ws_url_index % len(self._wss_nodes)]
            try:
                logger.info(f"链上WebSocket连接中... {wss_url} (第{retry_count+1}次)")

                async with websockets.connect(
                    wss_url,
                    ping_interval=20,
                    ping_timeout=10,
                ) as ws:
                    # 发送 eth_subscribe JSON-RPC 请求
                    await ws.send(json.dumps({
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": [
                            "logs",
                            {
                                "topics": [
                                    TRANSFER_TOPIC,
                                    FACTORY_PADDED,  # from = Factory（向买家转出代币）
                                    None,            # to = 任意买家
                                ]
                            },
                        ],
                    }))

                    # 等待订阅确认，获取 sub_id
                    resp = json.loads(await ws.recv())
                    sub_id = resp.get("result")
                    if not sub_id:
                        logger.error(f"订阅失败: {resp}")
                        await asyncio.sleep(5)
                        continue

                    retry_count = 0
                    logger.info(f"链上订阅成功 sub_id={sub_id}")

                    # 持续接收推送事件
                    async for raw in ws:
                        try:
                            msg = json.loads(raw)

                            if msg.get("method") != "eth_subscription":
                                continue
                            params = msg.get("params", {})
                            if params.get("subscription") != sub_id:
                                continue

                            log = params.get("result", {})
                            token_address = log.get("address", "")

                            if not token_address or len(token_address) != 42:
                                continue
                            if token_address.lower() == FACTORY_ADDRESS_LC:
                                continue

                            tx_hash = log.get("transactionHash", "")
                            logger.debug(
                                f"链上Transfer: {token_address[:10]} "
                                f"tx={tx_hash[:10] if tx_hash else '?'}"
                            )
                            asyncio.create_task(self._on_token_activity(token_address))

                        except Exception as e:
                            logger.debug(f"链上日志解析异常: {e}")

            except Exception as e:
                retry_count += 1
                # 切换到下一个节点
                self._ws_url_index = (self._ws_url_index + 1) % len(self._wss_nodes)
                delay = min(5 * retry_count, 30)
                next_url = self._wss_nodes[self._ws_url_index % len(self._wss_nodes)]
                logger.warning(
                    f"链上WebSocket断开 [{wss_url}]: {e}，"
                    f"切换节点→{next_url}，{delay}s后重连"
                )
                await asyncio.sleep(delay)

    # ─────────────────────────────────────────────────────────────
    # Fallback: DB Watchlist Polling (補漏)
    # ─────────────────────────────────────────────────────────────

    async def _chain_polling_fallback(self):
        """热门代币轮询：进度 >= 60% 的代币每 10s 查一次"""
        logger.info("热门代币轮询 启动（10s间隔，进度60%+）...")

        while self._running:
            try:
                await asyncio.sleep(10)
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(FOUR_MEME_WATCHLIST_DDL)
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT token_address, token_name, token_symbol "
                        "FROM four_meme_watchlist "
                        "WHERE status = 'watching' AND progress >= 60 "
                        "ORDER BY progress DESC"
                    ) as cur:
                        rows = await cur.fetchall()

                if rows:
                    logger.info(f"🔥 高优先级扫描: {len(rows)}个进度60%+的代币")
                    for row in rows:
                        asyncio.create_task(
                            self._on_token_activity(row["token_address"], row["token_name"], row["token_symbol"])
                        )
            except Exception as e:
                logger.error(f"热门轮询异常: {e}")

    async def _cold_token_polling(self):
        """冷门代币轮询：进度 5-60% 的代币每 2min 查一次"""
        logger.info("冷门代币轮询 启动（2min间隔，进度5-60%）...")

        while self._running:
            try:
                await asyncio.sleep(120)
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(FOUR_MEME_WATCHLIST_DDL)
                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT token_address, token_name, token_symbol "
                        "FROM four_meme_watchlist "
                        "WHERE status = 'watching' AND progress >= 5 AND progress < 60 "
                        "AND (last_activity IS NULL OR last_activity < datetime('now', '-2 minutes'))"
                    ) as cur:
                        rows = await cur.fetchall()

                if rows:
                    logger.debug(f"低频扫描: {len(rows)}个代币（进度5-60%）")
                    sem = asyncio.Semaphore(5)
                    async def _check(row):
                        async with sem:
                            await self._on_token_activity(row["token_address"], row["token_name"], row["token_symbol"])
                    await asyncio.gather(*[_check(r) for r in rows])
            except Exception as e:
                logger.error(f"冷门轮询异常: {e}")

    async def _cleanup_dead_tokens(self):
        """每 10 分钟清理：创建超 30 分钟且进度 < 5% 的死币"""
        logger.info("死币清理任务 启动（10min间隔）...")

        while self._running:
            try:
                await asyncio.sleep(600)
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(FOUR_MEME_WATCHLIST_DDL)
                    await db.execute(
                        "UPDATE four_meme_watchlist SET status='dead' "
                        "WHERE status='watching' AND progress < 5 "
                        "AND created_at < datetime('now', '-30 minutes')"
                    )
                    await db.commit()

                    db.row_factory = aiosqlite.Row
                    async with db.execute(
                        "SELECT COUNT(*) as cnt FROM four_meme_watchlist "
                        "WHERE status = 'watching' "
                        "AND (progress > 5 OR last_activity > datetime('now', '-10 minutes'))"
                    ) as cur:
                        row = await cur.fetchone()
                    active_cnt = row["cnt"] if row else 0

                logger.info(f"死币清理完成，活跃监控代币: {active_cnt}个")
            except Exception as e:
                logger.error(f"死币清理异常: {e}")

    # ─────────────────────────────────────────────────────────────
    # Core: Token Activity Handler
    # ─────────────────────────────────────────────────────────────

    async def _on_token_activity(self, token_address: str, token_name: str = "", token_symbol: str = ""):
        """收到代币活动信号后的主处理流程"""
        # 地址基本校验 + 标准化（避免大小写不同导致重复）
        if not token_address or len(token_address) != 42 or not token_address.startswith("0x"):
            return
        try:
            token_address = AsyncWeb3.to_checksum_address(token_address)
        except Exception:
            return
        if token_address in self._processing:
            return
        self._processing.add(token_address)

        try:
            # 确保代币在 watchlist
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                await db.execute(
                    "INSERT OR IGNORE INTO four_meme_watchlist "
                    "(token_address, token_name, token_symbol) VALUES (?,?,?)",
                    (token_address, token_name, token_symbol),
                )
                await db.commit()

            # 读取募资进度
            progress = await self._get_progress(token_address)
            logger.debug(f"进度更新: {token_address[:10]} {token_symbol} = {progress:.1f}%")

            # 更新进度和名称
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                await db.execute(
                    """UPDATE four_meme_watchlist
                       SET progress = ?, last_activity = datetime('now'),
                           token_name   = CASE WHEN token_name   = '' THEN ? ELSE token_name   END,
                           token_symbol = CASE WHEN token_symbol = '' THEN ? ELSE token_symbol END
                       WHERE token_address = ?""",
                    (progress, token_name, token_symbol, token_address),
                )
                if progress >= 100:
                    await db.execute(
                        "UPDATE four_meme_watchlist SET status='graduated' WHERE token_address=?",
                        (token_address,),
                    )
                await db.commit()

            if progress >= 100:
                return  # 已毕业，跳过

            # 检查黄金窗口
            if not (self.progress_min <= progress <= self.progress_max):
                return

            # ── 防重复评估（支持进度涨10%后重评估）──
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                db.row_factory = aiosqlite.Row
                async with db.execute(
                    "SELECT evaluated_at_progress, reject_reason, status "
                    "FROM four_meme_watchlist WHERE token_address=?",
                    (token_address,),
                ) as cur:
                    row = await cur.fetchone()

            current_status  = (row["status"] or "watching") if row else "watching"
            last_eval_pct   = float(row["evaluated_at_progress"]) if row and row["evaluated_at_progress"] else 0.0
            last_reject     = (row["reject_reason"] or "") if row else ""

            # 永久拒绝（貔貅/黑名单）：不再评估
            if current_status == "rejected":
                return

            should_evaluate = (
                last_eval_pct == 0                  # 从未评估过
                or progress > last_eval_pct + 10    # 进度又涨了10%+
                or "GMGN" in last_reject            # 上次因GMGN缺失被拒，重试
            )

            if not should_evaluate:
                return

            # 已持仓则跳过
            if token_address in self.position_manager.positions:
                return

            self._evaluated.add(token_address)
            logger.info(
                f"🎯 黄金窗口: {token_name or token_symbol or token_address[:10]} "
                f"进度={progress:.1f}% 上次评估={last_eval_pct:.0f}%"
            )

            # 记录评估时进度
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                await db.execute(
                    "UPDATE four_meme_watchlist SET evaluated_at_progress=? WHERE token_address=?",
                    (progress, token_address),
                )
                await db.commit()

            await self._evaluate_and_buy(token_address, token_name or token_symbol, progress)

        except Exception as e:
            logger.error(f"处理异常 {token_address[:10]}: {e}")
        finally:
            self._processing.discard(token_address)

    # ─────────────────────────────────────────────────────────────
    # Evaluation: Security Check + Buy Decision
    # ─────────────────────────────────────────────────────────────

    async def _evaluate_and_buy(self, token_address: str, token_name: str, progress: float):
        """
        方案B评分逻辑：
        - 基础分70（进度75%+本身是市场筛选）
        - 貔貅/黑名单：一票否决，永久拒绝
        - GMGN：有数据则评分，无数据不扣分（社交媒体完全移除惩罚）
        - 进度：核心加分（最高+20）
        - 低分：临时拒绝，等进度再涨10%重新评估
        """
        score = 70
        reasons = []
        deployer = ""

        try:
            # ===== 1. 貔貅检测（一票否决，最优先）=====
            hp = await self.security_checker.check_honeypot_is(token_address)
            if hp:
                if hp.get("is_honeypot"):
                    await self._reject(token_address, "貔貅", 0, permanent=True)
                    return
                sell_tax = hp.get("sell_tax") or 0
                if sell_tax > 15:
                    await self._reject(token_address, f"卖出税{sell_tax:.0f}%", 0, permanent=True)
                    return
                buy_tax = hp.get("buy_tax") or 0
                if buy_tax > 10:
                    score -= 10; reasons.append(f"买入税{buy_tax:.0f}%-10")
                elif sell_tax <= 1 and buy_tax <= 1:
                    score += 8; reasons.append("零税+8")

            # ===== 2. Deployer黑名单（一票否决）=====
            deployer = await self._get_deployer(token_address)
            if deployer:
                bl_reason = await self.security_checker.blacklist_manager.check_deployer(deployer)
                if bl_reason:
                    await self._reject(token_address, f"Deployer黑名单:{bl_reason}", 0, permanent=True)
                    return

            # ===== 3. 进度加分（核心逻辑，不依赖任何API）=====
            if 88 <= progress <= 92:
                score += 20; reasons.append(f"超级窗口{progress:.0f}%+20")
            elif 82 <= progress < 88:
                score += 15; reasons.append(f"黄金窗口{progress:.0f}%+15")
            elif 75 <= progress < 82:
                score += 8;  reasons.append(f"早期窗口{progress:.0f}%+8")

            # ===== 4. GMGN检测（有数据加分，无数据不扣分）=====
            gmgn = await self.security_checker._check_gmgn_signals(token_address)
            holders       = gmgn.get("holder_count")
            top10         = gmgn.get("top_10_holder_rate")
            creator_hold  = gmgn.get("creator_hold_rate")
            creator_total = gmgn.get("creator_created_count")
            bot_degen     = gmgn.get("bot_degen_count")
            gmgn_has_data = any(v is not None for v in [holders, top10, bot_degen])

            # GMGN危险警告：永久拒绝
            if gmgn.get("is_show_alert") or gmgn.get("flags"):
                await self._reject(token_address, f"GMGN危险警告:{gmgn.get('flags')}", 0, permanent=True)
                return

            if gmgn_has_data:
                # 一票否决（阈值放宽）
                if holders is not None and holders < 10:
                    await self._reject(token_address, f"持有人不足10({holders})", 0, permanent=True)
                    return
                if top10 is not None and top10 > 85:
                    await self._reject(token_address, f"Top10过度集中{top10:.0f}%", 0, permanent=True)
                    return
                if creator_total is not None and creator_total >= 50:
                    await self._reject(token_address, f"Dev疑似Serial Rugger({creator_total})", 0, permanent=True)
                    return

                # 持有人加分
                if holders is not None:
                    if holders >= 100:  score += 15; reasons.append(f"持有人{holders}+15")
                    elif holders >= 50: score += 10; reasons.append(f"持有人{holders}+10")
                    elif holders >= 20: score += 5;  reasons.append(f"持有人{holders}+5")

                # Bot Degen加分
                if bot_degen is not None:
                    if bot_degen >= 100:  score += 10; reasons.append(f"BotDegen{bot_degen}+10")
                    elif bot_degen >= 50: score += 7;  reasons.append(f"BotDegen{bot_degen}+7")
                    elif bot_degen >= 20: score += 3

                # Dev持仓（有数据才评判）
                if creator_hold is not None:
                    if creator_hold > 30:   score -= 15; reasons.append(f"Dev持仓{creator_hold:.0f}%-15")
                    elif creator_hold > 10: score -= 8;  reasons.append(f"Dev持仓{creator_hold:.0f}%-8")
                    elif creator_hold < 5:  score += 5;  reasons.append(f"Dev<5%+5")

                # Top10集中度
                if top10 is not None:
                    if top10 > 60:   score -= 10; reasons.append(f"Top10集中{top10:.0f}%-10")
                    elif top10 < 20: score += 8;  reasons.append(f"Top10分散{top10:.0f}%+8")
                    elif top10 < 30: score += 4

                # 社交媒体：有加分，没有不扣分（方案B核心）
                if gmgn.get("has_twitter"):  score += 8; reasons.append("Twitter+8")
                if gmgn.get("has_telegram"): score += 5; reasons.append("Telegram+5")
                if gmgn.get("has_discord"):  score += 5; reasons.append("Discord+5")
            else:
                logger.warning(f"[scanner] GMGN数据全为None，跳过GMGN评分（不扣分）: {token_name}")

            score = max(0, min(100, score))
            logger.info(
                f"评分: {token_name or token_address[:10]} score={score} "
                f"进度={progress:.1f}% GMGN={'有效' if gmgn_has_data else '无数据'} "
                f"原因={','.join(reasons) or '无'}"
            )

            if score < self.min_score:
                logger.info(f"分数不足: {token_name} score={score}<{self.min_score}，继续观察等待进度上涨")
                # 低分不永久拒绝：status保持watching，进度涨10%后自动重评估
                await self._reject(token_address, f"score={score}<{self.min_score}", score, permanent=False)
                return

            # 更新状态为 approved
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                await db.execute(
                    "UPDATE four_meme_watchlist SET score=?, status='approved' WHERE token_address=?",
                    (score, token_address),
                )
                await db.commit()

            # ── 买入 ──
            logger.info(f"买入: {token_name} score={score} 进度={progress:.1f}% 金额={self.buy_amount}BNB")
            base_amount = float(self.executor.config.get("trading", {}).get("buy_amount", 0.1))
            amount_multiplier = (self.buy_amount / base_amount) if base_amount > 0 else 0.5

            buy_result = await self.executor.buy_token(
                token_address=token_address,
                token_symbol=token_name,
                amount_multiplier=amount_multiplier,
                dex_name="four_meme",
            )

            if buy_result.get("status") == "success":
                amount_bnb_in = buy_result.get("amount_bnb_in", self.buy_amount)
                token_amount  = buy_result.get("amount", 0.0)
                buy_price     = amount_bnb_in / token_amount if token_amount > 0 else 0.0
                buy_gas_price = buy_result.get("buy_gas_price", 0)

                await self.position_manager.add_position(
                    token_address=token_address,
                    token_name=token_name,
                    buy_price=buy_price,
                    buy_amount_bnb=amount_bnb_in,
                    token_amount=token_amount,
                    buy_gas_price=buy_gas_price,
                    dex_data={},
                    pair_address="",
                    initial_liquidity_bnb=0.0,
                    dex_name="four_meme",
                    security_score=score,
                    deployer_address=deployer or "",
                    pregrad_buy=True,
                    pregrad_progress_at_buy=progress,
                )
                logger.success(f"预买入成功: {token_name} {amount_bnb_in:.4f}BNB 进度={progress:.1f}%")
            else:
                reason = buy_result.get("reason", "unknown")
                logger.warning(f"买入失败: {token_name} {reason}")
                self._evaluated.discard(token_address)  # 允许重试

        except Exception as e:
            logger.error(f"评估异常 {token_address[:10]}: {e}", exc_info=True)
            self._evaluated.discard(token_address)

    # ─────────────────────────────────────────────────────────────
    # Helpers
    # ─────────────────────────────────────────────────────────────

    async def _get_progress(self, token_address: str) -> float:
        """通过工厂合约的 0xe684626b 读取 BNB 募资进度 (slot5=target, slot8=raised)"""
        try:
            token_padded = "000000000000000000000000" + token_address[2:].lower()
            result = await self.w3.eth.call({
                "to": AsyncWeb3.to_checksum_address(FACTORY_ADDRESS),
                "data": bytes.fromhex(FACTORY_SEL + token_padded),
            })
            if not result or len(result) < 9 * 32:
                logger.warning(f"进度读取结果为空/过短 {token_address[:10]}")
                return 0.0
            raw   = result.hex()
            slot5 = int(raw[5 * 64:6 * 64], 16)   # graduation target (Wei)
            slot8 = int(raw[8 * 64:9 * 64], 16)   # BNB raised (Wei)
            if slot5 == 0:
                logger.warning(f"进度读取slot5=0(代币未在factory注册?) {token_address[:10]}")
                return 0.0
            return min(100.0, slot8 / slot5 * 100)
        except Exception as e:
            logger.warning(f"进度读取失败(RPC超时?) {token_address[:10]}: {e}")
            return 0.0

    async def _get_deployer(self, token_address: str) -> str:
        """通过工厂合约读取代币创建者地址 (slot0 = creator)"""
        try:
            token_padded = "000000000000000000000000" + token_address[2:].lower()
            result = await self.w3.eth.call({
                "to": AsyncWeb3.to_checksum_address(FACTORY_ADDRESS),
                "data": bytes.fromhex(FACTORY_SEL + token_padded),
            })
            if result and len(result) >= 32:
                raw      = result.hex()
                addr_hex = raw[24:64]   # last 20 bytes of slot0
                addr     = "0x" + addr_hex
                if addr != "0x" + "0" * 40:
                    return addr
        except Exception:
            pass
        return ""

    async def _reject(self, token_address: str, reason: str, score: int, permanent: bool = False):
        """
        permanent=True  → status='rejected'，永久跳过（貔貅/黑名单/GMGN警告）
        permanent=False → status='watching'，保持监控，进度涨10%后自动重评估（评分不足）
        """
        status = "rejected" if permanent else "watching"
        logger.info(f"{'永久' if permanent else '临时'}拒绝[{reason}]: {token_address[:10]}")
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                await db.execute(
                    "UPDATE four_meme_watchlist "
                    "SET status=?, reject_reason=?, score=? "
                    "WHERE token_address=?",
                    (status, reason, score, token_address),
                )
                await db.commit()
        except Exception:
            pass

    async def register_token(self, token_address: str, token_name: str = "", token_symbol: str = ""):
        """由 pair_listener 调用，注册新发现的代币并查询初始进度"""
        try:
            token_address = AsyncWeb3.to_checksum_address(token_address)
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                await db.execute(
                    "INSERT OR IGNORE INTO four_meme_watchlist "
                    "(token_address, token_name, token_symbol, status) VALUES (?,?,?,'watching')",
                    (token_address, token_name, token_symbol),
                )
                await db.commit()

            progress = await self._get_progress(token_address)

            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(FOUR_MEME_WATCHLIST_DDL)
                await db.execute(
                    "UPDATE four_meme_watchlist SET progress=? WHERE token_address=?",
                    (progress, token_address),
                )
                await db.commit()

            logger.info(
                f"[FourMemeScanner] 代币已注册: {token_name or token_symbol} "
                f"{token_address[:10]} 初始进度={progress:.1f}%"
            )
        except Exception as e:
            logger.error(f"register_token 异常 {token_address[:10]}: {e}")
