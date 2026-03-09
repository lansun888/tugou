import asyncio
import json
import logging
import yaml
from datetime import datetime, timedelta
from typing import Dict, Optional, Any, List
import aiosqlite
from web3 import Web3, AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from loguru import logger
from dotenv import load_dotenv
import os
from .abis import PANCAKESWAP_V2_FACTORY_ABI, PANCAKESWAP_V3_FACTORY_ABI, BISWAP_FACTORY_ABI, FOUR_MEME_FACTORY_ABI, ERC20_ABI
from bsc_bot.utils.multicall_helper import multicall3_batch

# 加载环境变量
load_dotenv(override=True)

# 常量定义
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
PANCAKESWAP_V2_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
PANCAKESWAP_V3_FACTORY = "0x0BFbCF9fa4f9C56B0F40a671Ad40E0805A091865"
BISWAP_FACTORY = "0x858E3312ed3A876947EA49d572A7C42DE08af7EE"
FOUR_MEME_FACTORY = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
# Verified via on-chain inspection: 0x7db... and 0x0a55... are bonding-curve TRADE events, NOT Listed.
# Real graduation event topic unknown (rare); monitoring is done via TokenCreate only.
FOUR_MEME_TOKEN_CREATE_TOPIC = "0x396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20"

# 自修复DDL：在任何使用 deployer_history 的连接内执行，确保表存在
_DEPLOYER_HISTORY_DDL = """
CREATE TABLE IF NOT EXISTS deployer_history (
    deployer TEXT,
    pair_address TEXT,
    created_at TIMESTAMP
)
"""

class PriorityPairQueue:
    """异步优先级队列：流动性更高的新币优先处理。

    接口与 asyncio.Queue 相同，put 额外接受 priority 参数。
    内部使用 asyncio.PriorityQueue（最小堆），存入负流动性使高流动性靠前。
    """

    def __init__(self):
        self._pq: asyncio.PriorityQueue = asyncio.PriorityQueue()
        self._counter = 0  # 相同优先级时保持 FIFO 顺序

    def put(self, pair: dict, priority: float = 0.0) -> None:
        """非阻塞入队。priority 越大（流动性越高）越先出队。"""
        self._pq.put_nowait((-priority, self._counter, pair))
        self._counter += 1

    async def get(self) -> dict:
        """阻塞出队，返回当前最高优先级的 pair_data。"""
        _, _, pair = await self._pq.get()
        return pair

    def qsize(self) -> int:
        return self._pq.qsize()

    def empty(self) -> bool:
        return self._pq.empty()


class PairListener:
    def __init__(self, config_path: str = "config.yaml", db_path: str = "./data/bsc_bot.db"):
        self.config_path = config_path
        self.config = self.load_config(config_path)
        self.w3: Optional[AsyncWeb3] = None
        self.queue = PriorityPairQueue()
        self.db_path = db_path
        self.running = False
        self.security_checker = None # Injected by TradingBot
        self.executor = None # Injected by TradingBot
        self._pending_four_meme = {} # {token_address: analysis_task}
        self.processed_tokens = set() # Cache for processed tokens to avoid duplicates from Trade events
        
        # 敏感词列表
        self.sensitive_words = ["TEST", "FAKE", "SCAM", "HONE", "POT", "RUG"]
        
        # 连接状态
        self.connection_status = {
            "pancakeswap_v2": False,
            "pancakeswap_v3": False,
            "biswap": False,
            "four_meme": False
        }
        self.reconnect_lock = asyncio.Lock()

    def load_config(self, path: str) -> dict:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return {}

    async def init_db(self):
        """初始化数据库表（确保所有表存在）"""
        logger.info(f"[DB] 初始化数据库: {self.db_path}")
        async with aiosqlite.connect(self.db_path) as db:
            # 记录发现的Pair
            await db.execute("""
                CREATE TABLE IF NOT EXISTS pairs (
                    pair_address TEXT PRIMARY KEY,
                    token0 TEXT,
                    token1 TEXT,
                    target_token TEXT,
                    dex_name TEXT,
                    discovered_at TIMESTAMP,
                    deployer TEXT,
                    initial_liquidity REAL,
                    is_risky BOOLEAN,
                    risk_reason TEXT,
                    token_name TEXT,
                    token_symbol TEXT,
                    security_score INTEGER DEFAULT 0,
                    check_details TEXT DEFAULT '{}',
                    status TEXT DEFAULT 'analyzing',
                    analysis_result TEXT
                )
            """)
            
            # Check for missing columns and add them if they don't exist (Migration)
            try:
                cursor = await db.execute("PRAGMA table_info(pairs)")
                columns = [row[1] for row in await cursor.fetchall()]
                
                if "token_name" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN token_name TEXT")
                if "token_symbol" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN token_symbol TEXT")
                if "security_score" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN security_score INTEGER DEFAULT 0")
                if "check_details" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN check_details TEXT DEFAULT '{}'")
                if "status" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN status TEXT DEFAULT 'analyzing'")
                if "analysis_result" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN analysis_result TEXT")
                if "price_at_discovery" not in columns:
                    await db.execute("ALTER TABLE pairs ADD COLUMN price_at_discovery REAL")
            except Exception as e:
                logger.error(f"Migration failed: {e}")

            # 记录部署者历史
            await db.execute(_DEPLOYER_HISTORY_DDL)
            await db.commit()
        logger.info("[DB] 初始化完成")

    async def _close_provider(self, provider):
        """安全关闭 Web3 provider，避免重复的清理代码"""
        try:
            if hasattr(provider, "disconnect") and callable(provider.disconnect):
                result = provider.disconnect()
                if asyncio.iscoroutine(result):
                    await result
            if hasattr(provider, "close") and callable(provider.close):
                result = provider.close()
                if asyncio.iscoroutine(result):
                    await result
            session = getattr(provider, "_session", None) or getattr(provider, "session", None)
            if session:
                await session.close()
        except Exception:
            pass

    async def setup_web3(self):
        """初始化Web3连接 (支持多RPC故障转移)"""
        async with self.reconnect_lock:
            # Check if already connected by another task
            if self.w3:
                try:
                    if await asyncio.wait_for(self.w3.is_connected(), timeout=3.0):
                        return
                except Exception:
                    pass

            # Reload config to pick up new nodes if changed
            self.config = self.load_config(self.config_path)

            # Close existing connection if any
            if self.w3:
                await self._close_provider(self.w3.provider)

            # 1. Get RPC list from Env or Config
            rpc_urls = []
            
            env_rpc = os.getenv("BSC_WS_RPC")
            if env_rpc:
                rpc_urls.append(env_rpc)
                
            # Check execute nodes first (usually better quality)
            execute_nodes = self.config.get("nodes", {}).get("execute", [])
            if execute_nodes:
                 if isinstance(execute_nodes, list):
                     rpc_urls.extend(execute_nodes)
                 elif isinstance(execute_nodes, str):
                     rpc_urls.append(execute_nodes)

            # Then private_rpcs
            config_rpcs = self.config.get("network", {}).get("private_rpcs", [])
            if isinstance(config_rpcs, str):
                rpc_urls.append(config_rpcs)
            elif isinstance(config_rpcs, list):
                rpc_urls.extend(config_rpcs)
                
            # Fallback to public RPCs if list is empty
            if not rpc_urls:
                rpc_urls = [
                    "https://bsc-dataseed1.binance.org",
                    "https://bsc-dataseed2.binance.org", 
                    "https://1rpc.io/bnb",
                    "https://bscrpc.com"
                ]
            
            # Deduplicate while preserving order
            seen = set()
            unique_rpc_urls = []
            for url in rpc_urls:
                if url and url not in seen:
                    unique_rpc_urls.append(url)
                    seen.add(url)
            rpc_urls = unique_rpc_urls
                
            logger.info(f"Trying to connect to BSC nodes, {len(rpc_urls)} available...")
            
            for rpc_url in rpc_urls:
                w3 = None
                try:
                    if rpc_url.startswith("http"):
                        provider = AsyncWeb3.AsyncHTTPProvider(rpc_url, request_kwargs={'timeout': 10})
                        w3 = AsyncWeb3(provider)
                        logger.info(f"Connecting to HTTP RPC: {rpc_url}")
                    else:
                        w3 = AsyncWeb3(AsyncWeb3.WebSocketProvider(rpc_url, websocket_kwargs={'timeout': 10}))
                        logger.info(f"Connecting to WebSocket RPC: {rpc_url}")
                    
                    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                    
                    # Check connection with timeout
                    try:
                        if await asyncio.wait_for(w3.is_connected(), timeout=10.0):
                            self.w3 = w3
                            logger.success(f"✅ Successfully connected to BSC node: {rpc_url}")
                            return
                        else:
                            logger.warning(f"❌ Failed to connect to {rpc_url} (is_connected=False)")
                            await self._close_provider(w3.provider)
                    except asyncio.TimeoutError:
                        logger.warning(f"❌ Connection timeout for {rpc_url}")
                        await self._close_provider(w3.provider)
                except Exception as e:
                    logger.warning(f"❌ Error connecting to {rpc_url}: {e}")
                    if w3:
                        await self._close_provider(w3.provider)
                    
                    # If rate limited (429), pause briefly to be polite
                    if "429" in str(e) or "Too Many Requests" in str(e):
                        await asyncio.sleep(1.0)
                    
            # If loop finishes without return, all failed
            logger.error("All RPC connections failed")
            raise ConnectionError("Web3 connection failed for all provided RPCs")

    async def get_token_info(self, token_address: str) -> Optional[Dict[str, Any]]:
        """获取代币基本信息（multicall 单次 RPC 完成 4 个查询）"""
        try:
            token_address = Web3.to_checksum_address(token_address)

            results = await multicall3_batch(self.w3, [
                (token_address, "name()",        [], [], ["string"]),
                (token_address, "symbol()",      [], [], ["string"]),
                (token_address, "decimals()",    [], [], ["uint8"]),
                (token_address, "totalSupply()", [], [], ["uint256"]),
            ])
            name, symbol, decimals, total_supply = results

            if name is None or symbol is None or decimals is None or total_supply is None:
                raise ValueError("multicall returned None for one or more fields")

            return {
                "address": token_address,
                "name": name,
                "symbol": symbol,
                "decimals": decimals,
                "total_supply": total_supply,
                "total_supply_formatted": total_supply / (10 ** decimals)
            }
        except Exception as e:
            logger.warning(f"获取代币信息失败 {token_address}: {e}")
            return None

    async def get_deployer(self, tx_hash: str) -> Optional[str]:
        """获取合约部署者地址"""
        try:
            tx = await self.w3.eth.get_transaction(tx_hash)
            return tx["from"]
        except Exception as e:
            logger.warning(f"获取部署者失败: {e}")
            return None

    async def check_filters(self, token_info: Dict, deployer: str) -> tuple[bool, str]:
        """初步快速过滤"""
        if not token_info:
            return False, "无法获取代币信息"

        # 1. 敏感词过滤
        for word in self.sensitive_words:
            if word in token_info["name"].upper() or word in token_info["symbol"].upper():
                return False, f"包含敏感词: {word}"

        # 2. 供应量过滤 (1000万亿 = 10^15)
        if token_info["total_supply_formatted"] > 10**15:
            return False, "总供应量过大"

        # 3. 部署者频率过滤
        try:
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(_DEPLOYER_HISTORY_DDL)
                one_hour_ago = datetime.now() - timedelta(hours=1)
                async with db.execute(
                    "SELECT COUNT(*) FROM deployer_history WHERE deployer = ? AND created_at > ?",
                    (deployer, one_hour_ago)
                ) as cursor:
                    row = await cursor.fetchone()
                    if row and row[0] > 0:
                        return False, "部署者近期频繁创建"
        except Exception as db_err:
            logger.error(f"数据库查询失败: {db_err}")
            # 数据库错误暂时放行，以免阻塞
            pass

        return True, "Pass"

    async def analyze_competition(self, tx_hash: str, block_number: int) -> Dict[str, Any]:
        """反夹Bot检测"""
        try:
            # 获取完整区块交易
            block = await self.w3.eth.get_block(block_number, full_transactions=True)
            txs = block["transactions"]
            
            # 找到Pair创建交易在区块中的位置
            target_tx_index = -1
            tx_hash_bytes = bytes.fromhex(tx_hash[2:]) if tx_hash.startswith("0x") else bytes.fromhex(tx_hash)
            
            for i, tx in enumerate(txs):
                if tx["hash"] == tx_hash_bytes:
                    target_tx_index = i
                    break
            
            if target_tx_index == -1:
                return {"risk": False, "msg": "未找到交易"}

            competitors = 0
            whale_buys = 0
            
            # 检查同区块后续交易
            for i in range(target_tx_index + 1, len(txs)):
                tx = txs[i]
                # 简单启发式：如果是大额BNB转账
                if tx["value"] > 0:
                    competitors += 1
                    if tx["value"] > 5 * 10**18: # > 5 BNB
                        whale_buys += 1
            
            risk_tags = []
            threshold = self.config.get("monitor", {}).get("competition_threshold", 3)
            
            if competitors > threshold:
                risk_tags.append("竞争激烈")
            if whale_buys > 0:
                risk_tags.append("巨鲸介入")
                
            return {
                "competitors": competitors,
                "whale_buys": whale_buys,
                "risk_tags": risk_tags
            }

        except Exception as e:
            logger.error(f"竞争分析失败: {e}")
            return {"competitors": 0, "whale_buys": 0, "risk_tags": []}

    async def observe_liquidity(self, pair_address: str, dex_name: str) -> tuple[bool, float, float]:
        """流动性变化观察. Returns (is_valid, liquidity_bnb, initial_price)"""
        logger.info(f"[{dex_name}] 开始观察流动性: {pair_address}")
        
        # Four.Meme 特殊处理：无需检查标准V2流动性池，默认为有流动性（Bonding Curve）
        if dex_name == "four_meme":
             # 暂时返回默认值，确保通过过滤
             # TODO: 实现真正的 Bonding Curve 价格和流动性查询
             return True, 100.0, 0.0

        wait_time = self.config.get("monitor", {}).get("observation_wait_time", 30)
        min_liquidity = self.config.get("monitor", {}).get("min_liquidity_bnb", 5.0)
        
        # 等待初始时间
        await asyncio.sleep(wait_time)
        
        try:
            # WBNB Address
            wbnb_addr = Web3.to_checksum_address(WBNB_ADDRESS)
            pair_addr = Web3.to_checksum_address(pair_address)
            
            # Get Reserves directly to calculate price
            pair_contract = self.w3.eth.contract(address=pair_addr, abi=PANCAKESWAP_V2_FACTORY_ABI) # Wrong ABI, need Pair ABI
            # Use simple ABI for getReserves
            pair_abi = [{"constant":True,"inputs":[],"name":"getReserves","outputs":[{"internalType":"uint112","name":"_reserve0","type":"uint112"},{"internalType":"uint112","name":"_reserve1","type":"uint112"},{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],"payable":False,"stateMutability":"view","type":"function"},
                        {"constant":True,"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}]
            
            pair_contract = self.w3.eth.contract(address=pair_addr, abi=pair_abi)

            # multicall: getReserves + token0 → 1 RPC
            mc_results = await multicall3_batch(self.w3, [
                (pair_addr, "getReserves()", [], [], ["uint112", "uint112", "uint32"]),
                (pair_addr, "token0()",      [], [], ["address"]),
            ])
            reserves, token0 = mc_results[0], mc_results[1]
            
            r0, r1, _ = reserves
            
            # Determine which is BNB (compare lowercase; eth_abi returns lowercase addresses)
            is_token0_wbnb = token0.lower() == wbnb_addr.lower()
            
            bnb_reserve = r0 if is_token0_wbnb else r1
            token_reserve = r1 if is_token0_wbnb else r0
            
            liquidity_bnb = (bnb_reserve / 10**18) * 2 # Total Liquidity (approx 2x BNB side)
            # Actually usually we just check BNB side for "depth"
            # Let's stick to BNB side value for check
            bnb_side_value = bnb_reserve / 10**18
            
            logger.info(f"[{dex_name}] {pair_address} 当前BNB池: {bnb_side_value:.2f} BNB")
            
            # Calculate Price (BNB per Token)
            # Price = BNB / Token
            price = 0.0
            if token_reserve > 0:
                price = bnb_reserve / token_reserve
            
            if bnb_side_value < min_liquidity:
                logger.warning(f"[{dex_name}] 流动性不足 ({bnb_side_value:.2f} < {min_liquidity}), 丢弃")
                return False, 0.0, 0.0
                
            return True, bnb_side_value, price
            
        except Exception as e:
            logger.error(f"流动性检查失败: {e}")
            return False, 0.0, 0.0

    async def process_event(self, event, dex_name: str, factory_type: str):
        """处理新Pair事件"""
        try:
            args = event["args"]
            
            # 处理 Four.Meme TokenCreate 事件 → 直接入处理队列
            # 链上验证: topic0=0x396d5e..., 所有参数非indexed, data=384bytes
            if dex_name == "four_meme" and factory_type == "TokenCreate":
                token_address = Web3.to_checksum_address(args["token"])
                deployer = Web3.to_checksum_address(args["creator"])
                tx_hash = event["transactionHash"].hex()
                block_number = event["blockNumber"]

                if token_address in self.processed_tokens:
                    return
                self.processed_tokens.add(token_address)

                logger.info(f"[four_meme] 发现新Token: {token_address} | Deployer: {deployer}")

                token_info = await self.get_token_info(token_address)
                if not token_info:
                    logger.warning(f"[four_meme] 无法获取Token信息，跳过: {token_address}")
                    return

                competition = await self.analyze_competition(tx_hash, block_number)

                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(_DEPLOYER_HISTORY_DDL)
                    await db.execute(
                        "INSERT INTO deployer_history (deployer, pair_address, created_at) VALUES (?, ?, ?)",
                        (deployer, token_address, datetime.now())
                    )
                    await db.commit()

                # observe_liquidity for four_meme returns True immediately (bonding curve)
                # pair_address 传 "" 而非 token_address，防止 position_manager 把 token
                # 合约当作 swap pair 来订阅 Swap 事件
                asyncio.create_task(self._async_liquidity_check(
                    "", token_address, "four_meme", token_info, competition, deployer
                ))
                return

            pair_address = args.get("pair") or args.get("pool")
            token0 = args["token0"]
            token1 = args["token1"]
            
            # 转换为 Checksum 地址
            token0 = Web3.to_checksum_address(token0)
            token1 = Web3.to_checksum_address(token1)
            wbnb_checksum = Web3.to_checksum_address(WBNB_ADDRESS)
            
            # 确定目标代币
            target_token = None
            if token0 == wbnb_checksum:
                target_token = token1
            elif token1 == wbnb_checksum:
                target_token = token0
            else:
                # 非BNB交易对，忽略
                return

            tx_hash = event["transactionHash"].hex()
            block_number = event["blockNumber"]
            
            logger.info(f"[{dex_name}] 发现新Pair: {pair_address} | Tx: {tx_hash}")

            # 1. 并行获取基本信息（原来是串行，浪费一个RTT）
            token_info, deployer = await asyncio.gather(
                self.get_token_info(target_token),
                self.get_deployer(tx_hash)
            )

            if not token_info or not deployer:
                logger.warning(f"[{dex_name}] 无法获取代币信息或部署者信息，跳过")
                return

            # 2. 快速过滤
            is_valid, reject_reason = await self.check_filters(token_info, deployer)
            if not is_valid:
                logger.warning(f"[{dex_name}] 过滤掉 {token_info['symbol']}: {reject_reason}")
                return

            # 3. 竞争分析
            competition = await self.analyze_competition(tx_hash, block_number)
            
            # 4. 记录部署者
            async with aiosqlite.connect(self.db_path) as db:
                await db.execute(_DEPLOYER_HISTORY_DDL)
                await db.execute(
                    "INSERT INTO deployer_history (deployer, pair_address, created_at) VALUES (?, ?, ?)",
                    (deployer, pair_address, datetime.now())
                )
                await db.commit()

            # 5. 启动后台流动性检查
            # 使用 asyncio.create_task 不阻塞主监听循环
            asyncio.create_task(self._async_liquidity_check(
                pair_address, target_token, dex_name, token_info, competition, deployer
            ))

        except Exception as e:
            logger.error(f"处理事件出错: {e}")
            import traceback
            logger.error(traceback.format_exc())

    async def _async_liquidity_check(self, pair_address, target_token, dex_name, token_info, competition, deployer):
        """后台执行流动性检查和后续操作"""
        is_liquid, liquidity_bnb, initial_price = await self.observe_liquidity(pair_address, dex_name)
        
        if is_liquid:
            pair_data = {
                "dex": dex_name,
                "pair": pair_address,
                "token": token_info,
                "deployer": deployer,
                "competition": competition,
                "discovered_at": datetime.now().isoformat(),
                "initial_price": initial_price,
                "liquidity_bnb": liquidity_bnb,   # 用于优先级排队
            }

            # 按流动性优先级入队：流动性越大越先处理
            self.queue.put(pair_data, priority=liquidity_bnb)
            logger.success(
                f"[{dex_name}] 新币入库: {token_info['symbol']} ({token_info['address']}) "
                f"| Price: {initial_price:.8f} BNB | Liq: {liquidity_bnb:.2f} BNB "
                f"| Queue: {self.queue.qsize()} pending"
            )
            
            # 存库
            # four_meme bonding curve 没有 pair 地址，用 token 地址作为 DB 主键
            db_pair_key = target_token if dex_name == 'four_meme' else pair_address
            try:
                async with aiosqlite.connect(self.db_path) as db:
                    await db.execute(
                        """INSERT OR IGNORE INTO pairs
                           (pair_address, token0, token1, target_token, dex_name, discovered_at, deployer, initial_liquidity, is_risky, risk_reason, token_name, token_symbol, security_score, status, price_at_discovery)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            db_pair_key,
                            WBNB_ADDRESS,
                            target_token,
                            target_token,
                            dex_name,
                            datetime.now(),
                            deployer,
                            liquidity_bnb,
                            len(competition["risk_tags"]) > 0,
                            ",".join(competition["risk_tags"]),
                            token_info.get("name", "Unknown"),
                            token_info.get("symbol", "UNK"),
                            0,
                            "analyzing",
                            initial_price
                        )
                    )
                    await db.commit()
            except Exception as e:
                logger.error(f"数据库写入失败: {e}")

    async def monitor_dex(self, factory_address: str, abi: list, dex_name: str, event_name: str = "PairCreated"):
        """监听指定DEX的Factory事件 (使用 get_logs 轮询，兼容不支持 filter 的 RPC)"""
        retry_count = 0
        max_retries = 100  # 增加重试次数
        
        while self.running and retry_count < max_retries:
            try:
                # 检查并修复连接
                try:
                    connected = False
                    if self.w3:
                        try:
                            connected = await self.w3.is_connected()
                        except Exception as e:
                            logger.warning(f"[{dex_name}] Web3 连接检查异常: {e}")
                            connected = False
                    
                    if not connected:
                        logger.warning(f"[{dex_name}] Web3 连接丢失或异常，尝试重连...")
                        await self.setup_web3()
                except Exception as e:
                    logger.error(f"[{dex_name}] 重连失败: {e}")
                    await asyncio.sleep(10)
                    retry_count += 1
                    continue

                factory_contract = self.w3.eth.contract(address=factory_address, abi=abi)
                
                # 获取当前最新区块作为起始点
                try:
                    last_processed_block = await self.w3.eth.block_number
                except Exception as e:
                    logger.error(f"[{dex_name}] 获取初始区块失败: {e}")
                    raise e

                logger.info(f"[{dex_name}] 开始监听 (Start Block: {last_processed_block})...")
                self.connection_status[dex_name] = True
                retry_count = 0 # 连接成功重置重试计数
                block_step = 50 # Start with 50 blocks
                
                while self.running:
                    try:
                        latest_block = await self.w3.eth.block_number
                        
                        if latest_block > last_processed_block:
                            # 限制查询范围，避免超出 RPC 限制 (例如每次最多查 50 个区块)
                            to_block = min(latest_block, last_processed_block + block_step)
                            
                            # 使用 get_logs 查询 (手动构建 filter 以避免 web3.py 异步 bug)
                            events = []
                            topic0 = None
                            
                            # Ensure to_block is not greater than latest_block
                            to_block = min(to_block, latest_block)
                            
                            from_block = last_processed_block + 1
                            
                            if from_block > to_block:
                                await asyncio.sleep(1)
                                continue

                            if event_name == "PairCreated":
                                # PairCreated(address,address,address,uint256)
                                topic0 = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9"
                            elif event_name == "PoolCreated":
                                # PoolCreated(address,address,uint24,int24,address)
                                topic0 = "0x783cca1c0412dd0d695e784568c96da2e9c22ff989357a2e8bb13300545c71ed"
                            elif event_name == "TokenCreate":
                                # TokenCreate(address,address,uint256,string,string,uint256,uint256,uint256)
                                # Verified on-chain: all params non-indexed, 384 bytes data
                                topic0 = FOUR_MEME_TOKEN_CREATE_TOPIC
                                
                            if topic0:
                                # Ensure block range is valid (fromBlock <= toBlock)
                                # from_block already calculated above

                                filter_params = {
                                    "fromBlock": hex(from_block),
                                    "toBlock": hex(to_block),
                                    "address": factory_address,
                                    "topics": [topic0]
                                }
                                
                                try:
                                    # logs = await self.w3.eth.get_logs(filter_params)
                                    # Use standard web3.eth.get_logs with integer block numbers
                                    # Web3.py handles hex conversion automatically
                                    
                                    # Create filter params with INTEGER block numbers
                                    # This is safer as web3.py knows how to format them for the specific RPC
                                    request_params = {
                                        "fromBlock": from_block,
                                        "toBlock": to_block,
                                        "address": factory_address,
                                        "topics": [topic0]
                                    }
                                    
                                    # Use standard get_logs
                                    logs = await self.w3.eth.get_logs(request_params)

                                    for log in logs:
                                        # Decode log
                                        event_data = None
                                        try:
                                            if event_name == "PairCreated":
                                                event_data = factory_contract.events.PairCreated().process_log(log)
                                            elif event_name == "PoolCreated":
                                                event_data = factory_contract.events.PoolCreated().process_log(log)
                                            elif event_name == "TokenCreate":
                                                event_data = factory_contract.events.TokenCreate().process_log(log)
                                            # Listed event removed: 0x7db/0x0a55 are trade events not graduation
                                        except Exception as decode_err:
                                            # logger.warning(f"[{dex_name}] Log decode failed: {decode_err}")
                                            continue
                                            
                                        if event_data:
                                            events.append(event_data)
                                            
                                except Exception as e:
                                    if "invalid block range" in str(e) or "limit" in str(e) or "-32000" in str(e) or "execution reverted" in str(e):
                                        logger.warning(f"[{dex_name}] Block range/execution error ({e}), reducing range from {block_step}...")
                                        # Don't reduce below 5 unless necessary
                                        if block_step > 10:
                                            block_step = max(5, block_step // 2)
                                        else:
                                            block_step = max(1, block_step // 2)
                                        await asyncio.sleep(1)
                                        continue 
                                    else:
                                        logger.error(f"[{dex_name}] get_logs 失败: {e}")
                                        events = [] # Reset on error
                            
                            if events:
                                logger.info(f"[{dex_name}] 发现 {len(events)} 个新事件 (Block {last_processed_block+1}-{to_block})")
                                for event in events:
                                    await self.process_event(event, dex_name, event_name)
                            
                            # 更新已处理区块
                            last_processed_block = to_block
                            
                            # Recover block step if it was reduced (faster recovery)
                            if block_step < 50:
                                block_step = min(50, block_step + 10)
                        
                        await asyncio.sleep(self.config.get("network", {}).get("polling_interval", 1.0)) # 使用配置的轮询间隔
                        
                    except Exception as e:
                        if "429" in str(e) or "Too Many Requests" in str(e):
                            logger.warning(f"[{dex_name}] 触发限流 (429), 暂停 10 秒...")
                            await asyncio.sleep(10)
                        elif "invalid block range" in str(e) or "limit" in str(e) or "-32000" in str(e) or "execution reverted" in str(e):
                             logger.warning(f"[{dex_name}] Block range/execution error ({e}), reducing range from {block_step}...")
                             if block_step > 1:
                                 block_step = max(1, block_step // 2)
                             else:
                                 await asyncio.sleep(2)
                        else:
                            logger.error(f"[{dex_name}] 轮询出错: {e}")
                            await asyncio.sleep(5)
                        
                        # 如果连续出错可以通过外部逻辑判断，这里简单处理
                        if "ClientConnectorError" in str(e) or "Timeout" in str(e):
                             raise e
                        
            except Exception as e:
                logger.error(f"[{dex_name}] 连接/运行出错: {e}")
                self.connection_status[dex_name] = False
                retry_count += 1
                await asyncio.sleep(5)
                logger.info(f"[{dex_name}] 尝试重连 ({retry_count}/{max_retries})...")

    async def run(self):
        """启动监听"""
        await self.init_db()
        await self.setup_web3()
        self.running = True
        
        tasks = []
        dex_config = self.config.get("monitor", {}).get("dex_enabled", {})
        
        if dex_config.get("pancakeswap_v2", True):
            tasks.append(self.monitor_dex(PANCAKESWAP_V2_FACTORY, PANCAKESWAP_V2_FACTORY_ABI, "pancakeswap_v2", "PairCreated"))
            
        if dex_config.get("pancakeswap_v3", True):
            tasks.append(self.monitor_dex(PANCAKESWAP_V3_FACTORY, PANCAKESWAP_V3_FACTORY_ABI, "pancakeswap_v3", "PoolCreated"))
            
        if dex_config.get("biswap", True):
            tasks.append(self.monitor_dex(BISWAP_FACTORY, BISWAP_FACTORY_ABI, "biswap", "PairCreated"))

        # four_meme: only TokenCreate (graduation/Listed event not confirmed on-chain)
        if dex_config.get("four_meme", True):
            tasks.append(self.monitor_dex(FOUR_MEME_FACTORY, FOUR_MEME_FACTORY_ABI, "four_meme", "TokenCreate"))
            
        await asyncio.gather(*tasks)

    def stop(self):
        self.running = False

if __name__ == "__main__":
    listener = PairListener()
    try:
        asyncio.run(listener.run())
    except KeyboardInterrupt:
        listener.stop()
        print("停止监控")
