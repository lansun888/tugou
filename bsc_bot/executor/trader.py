import asyncio
import os
import time
import datetime
import json
import logging
import aiosqlite
import yaml
import aiohttp
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from web3.exceptions import ContractLogicError, TransactionNotFound
from dotenv import load_dotenv
from loguru import logger
from decimal import Decimal
from .node_manager import NodeManager

# 导入 ABI
from bsc_bot.monitor.abis import PANCAKESWAP_ROUTER_ABI, ERC20_ABI, PANCAKESWAP_V2_FACTORY_ABI, PANCAKESWAP_PAIR_ABI

# 加载环境变量
load_dotenv()

class BSCExecutor:
    def __init__(self, config_path="config.yaml", mode=None):
        self.config = self._load_config(config_path)
        
        # Mode Override
        if mode:
            self.config['mode'] = mode
        
        self.mode = self.config.get('mode', 'live')
        self.test_mode = self.config.get("network", {}).get("test_mode", False) or (self.mode == 'simulation')
        
        # Set trades table based on mode
        self.trades_table = "simulation_trades" if self.mode == "simulation" else "trades"
        
        self.w3 = None
        self.router = None
        self.account = None
        self.bought_tokens = set()  # 防重复买入
        self.db_path = "bsc_bot.db"
        
        # 核心合约地址
        self.ROUTER_ADDRESS = self.w3_to_checksum("0x10ED43C718714eb63d5aA57B78B54704E256024E")
        self.WBNB_ADDRESS = self.w3_to_checksum("0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c")
        
        # 数据库路径统一（使用绝对路径，避免 cwd 影响）
        if config_path:
            _base = os.path.dirname(os.path.abspath(config_path))
        else:
            _base = os.path.dirname(os.path.abspath(__file__))
        self.db_path = os.path.join(_base, "data", "bsc_bot.db")
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        
        # Node Manager & Nonce Cache
        self.node_manager = NodeManager(self.config)
        self.nonce_cache = {}
        self.nonce_lock = asyncio.Lock()
        
        # Price Cache
        self.last_bnb_price = 600.0
        self.last_bnb_price_time = 0
        
        # Metadata Cache to reduce RPC calls
        self.token_decimals_cache = {}
        self.pair_address_cache = {} # token_address -> pair_address
        self.pair_token0_cache = {} # pair_address -> token0_address
        self.factory_address = None
        
        # Locks
        self.bnb_price_lock = asyncio.Lock()

        
    def _load_config(self, path):
        """加载配置文件"""
        try:
            with open(path, "r", encoding="utf-8") as f:
                return yaml.safe_load(f)
        except Exception as e:
            logger.error(f"加载配置文件失败: {e}")
            return {}
            
    def w3_to_checksum(self, address):
        """转换为 Checksum 地址"""
        return AsyncWeb3.to_checksum_address(address)

    async def init_executor(self):
        """初始化执行器 (Web3连接, 钱包, 数据库)"""
        # Close existing session if any
        if self.w3 and hasattr(self.w3.provider, '_session'):
            try:
                await self.w3.provider._session.close()
            except:
                pass

        # 1. 初始化 Web3 连接 (MEV保护：使用私有RPC)
        rpcs = self.config.get("network", {}).get("private_rpcs", [])
        
        if isinstance(rpcs, str):
            rpcs = [rpcs]
            
        if not rpcs:
            rpcs = [
                "https://bsc-dataseed1.binance.org",
                "https://bsc-dataseed2.binance.org",
                "https://1rpc.io/bnb",
                "https://bscrpc.com"
            ] # 默认回退
            
        # 尝试连接 RPC
        for rpc in rpcs:
            w3 = None
            try:
                if isinstance(rpc, str) and rpc.startswith("http"):
                    provider = AsyncWeb3.AsyncHTTPProvider(rpc, request_kwargs={'timeout': 10})
                    w3 = AsyncWeb3(provider)
                else:
                    w3 = AsyncWeb3(AsyncWeb3.WebSocketProvider(rpc, websocket_kwargs={'timeout': 10}))
                
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

                try:
                    if await asyncio.wait_for(w3.is_connected(), timeout=10.0):
                        self.w3 = w3
                        logger.info(f"✅ 成功连接到 RPC: {rpc}")
                        break
                    else:
                        logger.warning(f"❌ 连接失败 (is_connected=False): {rpc}")
                        if hasattr(w3.provider, '_session') and w3.provider._session:
                            await w3.provider._session.close()
                except asyncio.TimeoutError:
                    logger.warning(f"❌ 连接超时: {rpc}")
                    if hasattr(w3.provider, '_session') and w3.provider._session:
                        await w3.provider._session.close()
            except Exception as e:
                logger.warning(f"❌ 连接 RPC {rpc} 异常: {e}")
                if w3 and hasattr(w3.provider, '_session') and w3.provider._session:
                    try:
                        await w3.provider._session.close()
                    except:
                        pass
                
        if not self.w3 or not await self.w3.is_connected():
            logger.error("无法连接到任何 RPC 节点")
            raise ConnectionError("RPC 连接失败")
            
        # 注入 POA 中间件 (BSC 需要) - Done inside loop
        # self.w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        
        # 2. 初始化钱包
        is_test_mode = self.test_mode
        private_key = os.getenv("WALLET_PRIVATE_KEY")
        
        if not private_key:
            if is_test_mode:
                logger.warning("测试模式: 未配置私钥，将使用随机生成地址进行模拟")
                self.account = self.w3.eth.account.create()
            else:
                logger.error("未找到 WALLET_PRIVATE_KEY 环境变量")
                raise ValueError("私钥未配置 (非测试模式必填)")
        else:
            self.account = self.w3.eth.account.from_key(private_key)
            
        logger.info(f"加载钱包地址: {self.account.address} (Test Mode: {is_test_mode})")
        
        # 3. 初始化 Router 合约
        self.test_mode = is_test_mode  # Store test_mode for API access
        self.router = self.w3.eth.contract(address=self.ROUTER_ADDRESS, abi=PANCAKESWAP_ROUTER_ABI)
        
        # 4. 初始化数据库
        await self._init_db()

        # 5. Start Node Monitor & Init Nonce
        await self.node_manager.start_monitoring()
        if self.account:
            await self.refresh_nonce()
        
    async def _init_db(self):
        """初始化数据库表"""
        async with aiosqlite.connect(self.db_path) as db:
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
                    pnl_percentage REAL DEFAULT 0,
                    sell_percentage REAL DEFAULT 100,
                    expected_amount REAL DEFAULT 0,
                    actual_amount REAL DEFAULT 0,
                    slippage_pct REAL DEFAULT 0,
                    slippage_bnb REAL DEFAULT 0,
                    gas_used INTEGER DEFAULT 0,
                    gas_price_gwei REAL DEFAULT 0,
                    gas_cost_bnb REAL DEFAULT 0,
                    total_cost_bnb REAL DEFAULT 0
                )
            """)
            
            # Migration: Check for missing columns
            try:
                cursor = await db.execute(f"PRAGMA table_info({self.trades_table})")
                columns = [row[1] for row in await cursor.fetchall()]
                
                if "token_address" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN token_address TEXT")
                if "token_name" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN token_name TEXT")
                if "token_symbol" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN token_symbol TEXT")
                if "action" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN action TEXT")
                if "amount_token" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN amount_token TEXT")
                if "amount_bnb" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN amount_bnb TEXT")
                if "price_bnb" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN price_bnb TEXT")
                if "price_usd" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN price_usd TEXT")
                if "tx_hash" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN tx_hash TEXT")
                if "status" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN status TEXT")
                if "created_at" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP")
                if "note" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN note TEXT")
                if "pnl_bnb" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN pnl_bnb REAL DEFAULT 0")
                if "pnl_percentage" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN pnl_percentage REAL DEFAULT 0")
                if "sell_percentage" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN sell_percentage REAL DEFAULT 100")
                    
                # New Columns for Slippage Tracking
                if "expected_amount" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN expected_amount REAL DEFAULT 0")
                if "actual_amount" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN actual_amount REAL DEFAULT 0")
                if "slippage_pct" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN slippage_pct REAL DEFAULT 0")
                if "slippage_bnb" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN slippage_bnb REAL DEFAULT 0")
                if "gas_used" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN gas_used INTEGER DEFAULT 0")
                if "gas_price_gwei" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN gas_price_gwei REAL DEFAULT 0")
                if "gas_cost_bnb" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN gas_cost_bnb REAL DEFAULT 0")
                if "total_cost_bnb" not in columns:
                    await db.execute(f"ALTER TABLE {self.trades_table} ADD COLUMN total_cost_bnb REAL DEFAULT 0")
            except Exception as e:
                logger.error(f"Trade table migration failed: {e}")

            # 加载已买入代币到内存 set
            async with db.execute(f"SELECT token_address FROM {self.trades_table} WHERE action='buy' AND status='success'") as cursor:
                rows = await cursor.fetchall()
                for row in rows:
                    self.bought_tokens.add(row[0].lower())
            await db.commit()

    async def _get_gas_price(self):
        """根据策略获取 Gas Price"""
        try:
            base_gas = await self.w3.eth.gas_price
            
            gas_config = self.config.get("trading", {}).get("gas", {})
            mode = gas_config.get("mode", "normal")
            
            if mode == "frontrun":
                multiplier = gas_config.get("frontrun_multiplier", 1.5)
            else:
                multiplier = gas_config.get("normal_multiplier", 1.15)
                
            final_gas = int(base_gas * multiplier)
            logger.debug(f"Gas 策略: {mode}, 基础: {base_gas}, 最终: {final_gas}")
            return final_gas
        except Exception as e:
            logger.error(f"获取 Gas Price 失败: {e}")
            return await self.w3.eth.gas_price

    async def refresh_nonce(self):
        """Fetch and cache current nonce"""
        if self.account:
            try:
                nonce = await self.w3.eth.get_transaction_count(self.account.address)
                async with self.nonce_lock:
                    self.nonce_cache[self.account.address] = nonce
                logger.debug(f"Nonce refreshed: {nonce}")
                return nonce
            except Exception as e:
                logger.error(f"Failed to refresh nonce: {e}")
        return None

    async def get_nonce(self):
        """Get cached nonce and increment"""
        if not self.account: return 0
        async with self.nonce_lock:
            if self.account.address not in self.nonce_cache:
                await self.refresh_nonce()
            
            nonce = self.nonce_cache.get(self.account.address, 0)
            self.nonce_cache[self.account.address] = nonce + 1
            return nonce

    async def pre_build_buy_tx(self, token_address, amount_bnb=None, slippage=None):
        """Pre-calculate transaction parameters (Gas, Nonce, Amounts) for fast execution"""
        t0 = time.time()
        try:
            token_address = self.w3_to_checksum(token_address)
            
            # Use config if not provided
            trading_conf = self.config.get("trading", {})
            if amount_bnb is None:
                amount_bnb = float(trading_conf.get("buy_amount", 0.01))
            if slippage is None:
                slippage = float(trading_conf.get("slippage", 12))
                
            amount_in_wei = self.w3.to_wei(amount_bnb, 'ether')
            
            # Parallel fetch: Gas Price, Nonce (from cache), AmountsOut
            # We use gather to run independent tasks
            tasks = []
            tasks.append(self._get_gas_price())
            tasks.append(self.get_nonce())
            
            # AmountsOut needs RPC
            path = [self.WBNB_ADDRESS, token_address]
            # Create a contract function call coroutine manually or wrap it
            async def get_amounts():
                return await self.router.functions.getAmountsOut(amount_in_wei, path).call()
            tasks.append(get_amounts())
            
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            gas_price = results[0] if not isinstance(results[0], Exception) else await self.w3.eth.gas_price
            nonce = results[1] if not isinstance(results[1], Exception) else await self.w3.eth.get_transaction_count(self.account.address)
            amounts_out = results[2]
            
            if isinstance(amounts_out, Exception):
                logger.warning(f"Pre-build failed to get amounts (token might be unbuyable): {amounts_out}")
                return None
                
            expected_token_out = amounts_out[-1]
            min_token_out = int(expected_token_out * (1 - slippage/100) * 0.99)
            
            deadline = int(time.time()) + 60
            
            # Construct tx params
            tx_params = {
                'from': self.account.address,
                'value': amount_in_wei,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': self.config.get("network", {}).get("chain_id", 56),
                'gas': 500000 # Default high gas to skip estimation
            }
            
            # Build contract function
            tx_func = self.router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                min_token_out, path, self.account.address, deadline
            )
            
            # Build the raw dict
            # Note: build_transaction usually does NOT do RPC if 'gas' is provided
            if self.test_mode:
                 # Simulation mode: skip balance check and build_transaction
                 built_tx = tx_params.copy()
                 built_tx['data'] = tx_func._encode_transaction_data()
                 built_tx['to'] = self.ROUTER_ADDRESS
            else:
                 built_tx = await tx_func.build_transaction(tx_params)
            
            logger.info(f"Transaction pre-built in {time.time()-t0:.3f}s")
            return {
                "tx": built_tx,
                "amount_out": expected_token_out,
                "min_out": min_token_out,
                "token_address": token_address
            }
            
        except Exception as e:
            logger.error(f"Pre-build transaction failed: {e}")
            return None

    async def fast_buy_token(self, pre_built_data):
        """Sign and send pre-built transaction using best node"""
        t0 = time.time()
        try:
            tx_params = pre_built_data["tx"]
            
            # Test/Simulation Mode Logic
            if self.test_mode:
                log_prefix = "[Simulation]" if self.mode == "simulation" else "[Test Mode]"
                logger.success(f"{log_prefix} Fast Buy Simulated! Amount Out: {pre_built_data['amount_out']}")
                
                tx_hash_sim = "simulated_fast_buy_" + str(int(time.time()))
                
                # Get token info from pre_built_data
                token_address = pre_built_data.get("token_address", "0x0000000000000000000000000000000000000000")
                token_symbol = pre_built_data.get("token_symbol", "UNKNOWN")
                
                amount_out_raw = pre_built_data["amount_out"]
                amount_out_human = float(self.w3.from_wei(amount_out_raw, 'ether'))
                amount_in = str(self.w3.from_wei(tx_params['value'], 'ether'))
                
                price_bnb = float(amount_in) / amount_out_human if amount_out_human > 0 else 0
                
                # Log simulated trade
                if self.mode == "simulation":
                     await self._log_trade(
                        token_address, token_symbol, "buy", 
                        str(amount_out_human), amount_in, 
                        tx_hash_sim, "success",
                        price_bnb=str(price_bnb),
                        token_symbol=token_symbol
                    )

                return {
                    "status": "success", 
                    "tx_hash": tx_hash_sim, 
                    "amount": amount_out_human,
                    "amount_bnb_in": float(self.w3.from_wei(tx_params['value'], 'ether')),
                    "buy_gas_price": tx_params.get('gasPrice', 0)
                }

            # Sign
            signed_tx = self.w3.eth.account.sign_transaction(tx_params, private_key=self.account.key)
            
            # Robust extraction of rawTransaction
            raw_tx_hex = None
            try:
                if hasattr(signed_tx, 'rawTransaction'):
                    raw_tx_hex = signed_tx.rawTransaction.hex()
                elif hasattr(signed_tx, 'raw_transaction'):
                    raw_tx_hex = signed_tx.raw_transaction.hex()
                elif isinstance(signed_tx, dict) and 'rawTransaction' in signed_tx:
                    raw_tx_hex = signed_tx['rawTransaction'].hex()
                else:
                    # Try to access it anyway, if it fails we catch it
                    raw_tx_hex = signed_tx.rawTransaction.hex()
            except Exception as e:
                logger.error(f"Failed to extract rawTransaction from {type(signed_tx)}: {e}")
                # Fallback: maybe signed_tx IS the raw transaction hex? Unlikely.
                raise e
            
            # Send via Best Node
            best_node = self.node_manager.get_best_node()
            logger.info(f"Sending fast tx via {best_node}...")
            
            # Use direct RPC call for speed
            async with aiohttp.ClientSession() as session:
                payload = {"jsonrpc": "2.0", "method": "eth_sendRawTransaction", "params": [raw_tx_hex], "id": 1}
                async with session.post(best_node, json=payload, timeout=5) as response:
                    resp_data = await response.json()
                    
            if "result" in resp_data:
                tx_hash = resp_data["result"]
                logger.success(f"Fast buy sent in {time.time()-t0:.3f}s! Hash: {tx_hash}")
                
                # Async Log trade (fire and forget or wait?)
                # We return immediately, log later
                asyncio.create_task(self._log_trade(
                    "PRE_BUILT", "PRE_BUILT", "buy", 
                    str(pre_built_data["amount_out"]), str(self.w3.from_wei(tx_params['value'], 'ether')), 
                    tx_hash, "success"
                ))
                
                return {
                    "status": "success", 
                    "tx_hash": tx_hash, 
                    "amount": float(pre_built_data["amount_out"]),
                    "amount_bnb_in": float(self.w3.from_wei(tx_params['value'], 'ether')),
                    "buy_gas_price": tx_params.get('gasPrice', 0)
                }
            else:
                logger.error(f"Fast buy failed: {resp_data}")
                return {"status": "failed", "reason": str(resp_data)}
                
        except Exception as e:
            logger.error(f"Fast buy exception: {e}")
            return {"status": "failed", "reason": str(e)}

    async def get_bnb_price_usd(self) -> float:
        """
        从Chainlink预言机获取BNB/USD价格
        这个直接读链上数据，不需要外网
        """
        # Chainlink BNB/USD Price Feed on BSC
        CHAINLINK_BNB_USD = "0x0567F2323251f0Aab15c8dFb1967E4e8A7D42aeE"
        
        AGGREGATOR_ABI = [
            {
                "inputs": [],
                "name": "latestRoundData",
                "outputs": [
                    {"name": "roundId", "type": "uint80"},
                    {"name": "answer", "type": "int256"},
                    {"name": "startedAt", "type": "uint256"},
                    {"name": "updatedAt", "type": "uint256"},
                    {"name": "answeredInRound", "type": "uint80"}
                ],
                "stateMutability": "view",
                "type": "function"
            }
        ]
        
        try:
            contract = self.w3.eth.contract(
                address=CHAINLINK_BNB_USD, 
                abi=AGGREGATOR_ABI
            )
            round_data = await contract.functions.latestRoundData().call()
            # Chainlink返回8位小数
            price = float(round_data[1]) / 10**8
            
            # Update cache
            async with self.bnb_price_lock:
                self.last_bnb_price = price
                self.last_bnb_price_time = time.time()
                
            return price
        except Exception as e:
            logger.warning(f"Chainlink查询失败: {e}，使用缓存值")
            return self.last_bnb_price or 600.0

    async def get_pair_liquidity(self, token_address):
        """Get liquidity (BNB Reserve) for Token/BNB pair"""
        try:
            token_address = self.w3_to_checksum(token_address)
            factory_address = await self.router.functions.factory().call()
            factory = self.w3.eth.contract(address=factory_address, abi=PANCAKESWAP_V2_FACTORY_ABI)
            
            pair_address = await factory.functions.getPair(token_address, self.WBNB_ADDRESS).call()
            
            if pair_address == "0x0000000000000000000000000000000000000000":
                return 0.0
                
            pair = self.w3.eth.contract(address=pair_address, abi=PANCAKESWAP_PAIR_ABI)
            reserves = await pair.functions.getReserves().call()
            token0 = await pair.functions.token0().call()
            
            # Identify which is BNB
            if token0 == self.WBNB_ADDRESS:
                bnb_reserve = reserves[0]
            else:
                bnb_reserve = reserves[1]
                
            return float(self.w3.from_wei(bnb_reserve, 'ether'))
        except Exception as e:
            logger.warning(f"Failed to get liquidity for {token_address}: {e}")
            return 0.0

    async def buy_token(self, token_address, token_symbol="UNKNOWN", amount_multiplier=1.0):
        """
        买入代币
        :param token_address: 代币地址
        :param token_symbol: 代币符号(用于日志)
        :param amount_multiplier: 买入金额倍数 (默认 1.0)
        """
        token_address = self.w3_to_checksum(token_address)
        
        # 0. 防重复买入检查
        if token_address.lower() in self.bought_tokens:
            logger.warning(f"代币 {token_symbol} 已在买入列表中，跳过")
            return {"status": "skipped", "reason": "already_bought"}
            
        try:
            # 读取配置
            trading_conf = self.config.get("trading", {})
            base_amount = float(trading_conf.get("buy_amount", 0.01))
            amount_bnb_in = base_amount * amount_multiplier
            slippage_percent = float(trading_conf.get("slippage", 12))
            deadline_secs = int(trading_conf.get("deadline_seconds", 45))
            
            # Test/Simulation Mode Logic
            if self.test_mode:
                log_prefix = "[Simulation]" if self.mode == "simulation" else "[Test Mode]"
                logger.success(f"{log_prefix} 买入成功: {token_symbol} ({token_address}) 花费 {amount_bnb_in} BNB")
                # 模拟逻辑
                try:
                    price_info = await self.get_token_price(token_address)
                    price_bnb = price_info.get("price_bnb", 0.001) if price_info else 0.001
                    if price_bnb == 0: price_bnb = 0.001
                except:
                    price_bnb = 0.001
                
                token_amount = amount_bnb_in / price_bnb
                
                # 记录模拟交易
                timestamp_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
                tx_hash_sim = f"SIM_{token_symbol}_{timestamp_str}"
                
                # Slippage/Gas Estimation
                est_slippage_pct = slippage_percent * 0.6
                # Back-calculate expected: actual = expected * (1 - slip) -> expected = actual / (1 - slip)
                # But here 'token_amount' is derived from price without slippage?
                # In simulation: token_amount = amount_bnb_in / price_bnb. This is "Theoretical Amount".
                # So expected = token_amount.
                # And actual = token_amount * (1 - slip)? 
                # Wait, simulation usually deducts on SELL. Buy is 0 friction?
                # User says: "Simulation slippage = Config * 0.6".
                # If Buy is 0 friction in my current code, then Actual = Expected. Slippage = 0.
                # But user wants to track "Simulated Cost".
                # I'll calculate it as if there WAS slippage.
                # Let's say Actual = token_amount. Expected = token_amount / (1 - 0.00).
                # User instructions: "模拟滑点 = 设置的滑点参数 × 0.6".
                # So I'll record slippage_pct = config * 0.6.
                # And calculate slippage_bnb based on that.
                est_slippage_bnb = amount_bnb_in * (est_slippage_pct / 100)
                
                est_gas_price_gwei = 3.0
                est_gas_used = 300000
                est_gas_cost_bnb = est_gas_used * est_gas_price_gwei * 1e9 / 1e18
                est_total_cost = est_slippage_bnb + est_gas_cost_bnb

                await self._log_trade(
                    token_address, token_symbol, "buy", 
                    str(token_amount), str(amount_bnb_in), 
                    tx_hash_sim, "success",
                    price_bnb=str(price_bnb),
                    token_symbol=token_symbol,
                    expected_amount=token_amount,
                    actual_amount=token_amount, # In sim buy we usually get full amount
                    slippage_pct=est_slippage_pct,
                    slippage_bnb=est_slippage_bnb,
                    gas_used=est_gas_used,
                    gas_price_gwei=est_gas_price_gwei,
                    gas_cost_bnb=est_gas_cost_bnb,
                    total_cost_bnb=est_total_cost
                )
                
                return {
                    "status": "success",
                    "tx_hash": tx_hash_sim,
                    "amount": token_amount,
                    "amount_bnb_in": amount_bnb_in,
                    "buy_gas_price": 5000000000 # Mock gas price
                }

            amount_in_wei = self.w3.to_wei(amount_bnb_in, 'ether')
            
            # 1. 检查余额
            balance = await self.w3.eth.get_balance(self.account.address)
            if balance < amount_in_wei:
                logger.error(f"BNB 余额不足: {self.w3.from_wei(balance, 'ether')} < {amount_bnb_in}")
                return {"status": "failed", "reason": "insufficient_balance"}

            # 2. 计算最小获得代币数量 (minAmountOut)
            # path: WBNB -> Token
            path = [self.WBNB_ADDRESS, token_address]
            amounts_out = await self.router.functions.getAmountsOut(amount_in_wei, path).call()
            expected_token_out = amounts_out[-1]
            
            # 最小数量 = 预期数量 * (1 - 滑点%) * 0.99 (安全余量)
            min_token_out = int(expected_token_out * (1 - slippage_percent/100) * 0.99)
            
            logger.info(f"准备买入 {token_symbol}: 投入 {amount_bnb_in} BNB, 预期获得 {expected_token_out}, 最小接受 {min_token_out}")

            # 4. 构建交易
            nonce = await self.get_nonce()
            gas_price = await self._get_gas_price()
            deadline = int(time.time()) + deadline_secs
            
            tx_func = self.router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                min_token_out, path, self.account.address, deadline
            )
            
            tx_params = {
                'from': self.account.address,
                'value': amount_in_wei,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': self.config.get("network", {}).get("chain_id", 56),
                'gas': 500000
            }
            
            # 5. 发送交易
            tx = await tx_func.build_transaction(tx_params)
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.account.key)
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = self.w3.to_hex(tx_hash)
            
            logger.info(f"买入交易已发送: {tx_hash_hex}, 等待确认...")
            
            receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            
            if receipt.status == 1:
                logger.success(f"买入 {token_symbol} 成功!")
                
                # 1. Calculate Actual Token Amount Received
                # Use _parse_transfer_log for precision
                raw_token_amount = self._parse_transfer_log(receipt, token_address)
                
                # Get decimals
                decimals = self.token_decimals_cache.get(token_address)
                if not decimals:
                    try:
                        token_contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
                        decimals = await token_contract.functions.decimals().call()
                        self.token_decimals_cache[token_address] = decimals
                    except:
                        decimals = 18
                
                token_amount = raw_token_amount / (10 ** decimals)
                
                # Fallback if parse fails (e.g. internal tx?) -> Use balance
                if token_amount == 0:
                     token_contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
                     token_balance = await token_contract.functions.balanceOf(self.account.address).call()
                     token_amount = token_balance / (10 ** decimals)
                
                self.bought_tokens.add(token_address.lower())
                
                # 2. Calculate Slippage
                # expected_token_out is raw integer from getAmountsOut
                expected_human = expected_token_out / (10 ** decimals)
                
                slippage_pct = 0.0
                if expected_human > 0:
                    slippage_pct = ((expected_human - token_amount) / expected_human) * 100
                
                # Slippage in BNB = Slippage% * AmountBNBIn
                slippage_bnb = amount_bnb_in * (slippage_pct / 100)
                
                # Check alert
                await self._check_slippage_alert(slippage_pct, token_symbol, "buy", tx_hash_hex)
                
                # 3. Calculate Gas Cost
                gas_used = receipt['gasUsed']
                # effectiveGasPrice is available in EIP-1559, otherwise use tx gasPrice
                effective_gas_price = receipt.get('effectiveGasPrice', gas_price)
                gas_price_gwei = effective_gas_price / 1e9
                gas_cost_bnb = (gas_used * effective_gas_price) / 1e18
                
                total_cost_bnb = slippage_bnb + gas_cost_bnb
                
                # 记录数据库
                await self._log_trade(
                    token_address, token_symbol, "buy", 
                    str(token_amount), str(amount_bnb_in), 
                    tx_hash_hex, "success",
                    expected_amount=expected_human,
                    actual_amount=token_amount,
                    slippage_pct=slippage_pct,
                    slippage_bnb=slippage_bnb,
                    gas_used=gas_used,
                    gas_price_gwei=gas_price_gwei,
                    gas_cost_bnb=gas_cost_bnb,
                    total_cost_bnb=total_cost_bnb
                )
                
                return {
                    "status": "success", 
                    "tx_hash": tx_hash_hex, 
                    "amount": float(token_amount),
                    "amount_bnb_in": float(amount_bnb_in),
                    "buy_gas_price": gas_price  # Return gas price for future use
                }
            else:
                logger.error(f"买入 {token_symbol} 失败 (Reverted)")
                await self._log_trade(
                    token_address, token_symbol, "buy", 
                    "0", str(amount_bnb_in), 
                    tx_hash_hex, "failed"
                )
                return {"status": "failed", "reason": "reverted", "tx_hash": tx_hash_hex}
                
        except Exception as e:
            logger.error(f"买入执行异常: {e}")
            # 简单重试逻辑 (如果是 gas 不足可以考虑重试，这里简化处理)
            return {"status": "failed", "reason": str(e)}

    async def sell_token(self, token_address, token_symbol="UNKNOWN", sell_percentage=100, slippage=None, gas_price=None, simulated_balance=None, pnl_bnb=0.0, pnl_percentage=0.0, manual_price=None, sell_percentage_real=None, cost_basis_bnb=None):
        """
        卖出代币
        :param token_address: 代币地址
        :param token_symbol: 代币符号
        :param sell_percentage: 卖出比例 (0-100) - 相对于当前余额
        :param slippage: 滑点 (None则使用配置)
        :param gas_price: Gas价格 (None则自动获取)
        :param simulated_balance: 模拟持仓数量 (仅限模拟模式)
        :param manual_price: 手动指定价格 (BNB), 用于模拟模式准确计算
        :param sell_percentage_real: 真实卖出比例 (相对于总持仓), 用于统计
        :param cost_basis_bnb: 本次卖出的成本 (BNB), 用于计算真实 PnL
        """
        token_address = self.w3_to_checksum(token_address)
        
        # Test/Simulation Mode Logic
        if self.test_mode:
            log_prefix = "[Simulation]" if self.mode == "simulation" else "[Test Mode]"
            logger.success(f"{log_prefix} 卖出成功: {token_symbol}")
            
            # Calculate simulated amounts
            sell_amount = 0
            if simulated_balance:
                sell_amount = float(simulated_balance) * (sell_percentage / 100)
            else:
                sell_amount = 1000.0 # Mock default if not provided
                
            # Get current price for realistic simulation
            price_bnb = 0.0
            if manual_price is not None and manual_price > 0:
                price_bnb = float(manual_price)
            else:
                try:
                    price_info = await self.get_token_price(token_address)
                    price_bnb = price_info.get("price_bnb", 0.0) if price_info else 0.0
                except:
                    price_bnb = 0.0
            
            # If price is still 0, use a very small fallback or 0 (don't use 0.001 which is huge for meme coins)
            # Better to have 0 BNB amount than wrong huge amount
            if price_bnb == 0:
                 logger.warning(f"无法获取 {token_symbol} 价格，模拟卖出金额可能为0")

            # Apply slippage/tax deduction for realistic simulation
            # User requirement: Simulation slippage = Config * 0.6
            config_slippage = float(self.config.get("trading", {}).get("slippage", 15))
            est_slippage_pct = config_slippage * 0.6
            
            estimated_bnb_raw = sell_amount * price_bnb
            estimated_bnb = estimated_bnb_raw * (1 - est_slippage_pct/100)
            
            slippage_bnb = estimated_bnb_raw - estimated_bnb
            
            # Gas Estimation
            est_gas_used = 300000
            est_gas_price_gwei = 3.0
            est_gas_cost_bnb = est_gas_used * est_gas_price_gwei * 1e9 / 1e18
            
            total_cost_bnb = slippage_bnb + est_gas_cost_bnb
            
            sim_status = "success"
            if estimated_bnb < 0.000001:
                sim_status = "failed_rug"
                estimated_bnb = 0.0
            
            # Calculate PnL if cost_basis is provided
            if cost_basis_bnb is not None and cost_basis_bnb > 0:
                pnl_bnb = estimated_bnb - cost_basis_bnb
                pnl_percentage = (pnl_bnb / cost_basis_bnb) * 100
                
            final_sell_pct = sell_percentage_real if sell_percentage_real is not None else sell_percentage
            
            logger.info(f"[Simulation] Sell {token_symbol}: Raw={estimated_bnb_raw:.6f} BNB, Deducted({est_slippage_pct}%)={estimated_bnb:.6f} BNB, Status={sim_status}")
            
            timestamp_str = datetime.datetime.now().strftime("%Y%m%d%H%M%S")
            tx_hash_sim = f"SIM_{token_symbol}_{timestamp_str}"
            await self._log_trade(
                token_address, token_symbol, "sell",
                str(sell_amount), str(estimated_bnb),
                tx_hash_sim, sim_status,
                price_bnb=str(price_bnb),
                token_symbol=token_symbol,
                pnl_bnb=pnl_bnb,
                pnl_percentage=pnl_percentage,
                sell_percentage=final_sell_pct,
                expected_amount=estimated_bnb_raw,
                actual_amount=estimated_bnb,
                slippage_pct=est_slippage_pct,
                slippage_bnb=slippage_bnb,
                gas_used=est_gas_used,
                gas_price_gwei=est_gas_price_gwei,
                gas_cost_bnb=est_gas_cost_bnb,
                total_cost_bnb=total_cost_bnb
            )
            
            return {
                "status": "success", 
                "tx_hash": tx_hash_sim, 
                "amount_bnb": estimated_bnb
            }

        try:
            # 初始化 Token 合约
            token_contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
            
            # 1. 获取余额
            balance = await token_contract.functions.balanceOf(self.account.address).call()
            if balance == 0:
                logger.warning(f"代币 {token_symbol} 余额为 0，无法卖出")
                return {"status": "failed", "reason": "zero_balance"}
                
            sell_amount = int(balance * (sell_percentage / 100))
            if sell_amount == 0:
                return {"status": "failed", "reason": "amount_too_small"}
                
            logger.info(f"准备卖出 {token_symbol}: 数量 {sell_amount} ({sell_percentage}%)")
            
            # 2. 检查并授权 (Approve)
            allowance = await token_contract.functions.allowance(self.account.address, self.ROUTER_ADDRESS).call()
            if allowance < sell_amount:
                logger.info(f"授权额度不足 ({allowance} < {sell_amount})，正在授权...")
                await self._approve_token(token_contract, token_address)
                
            # 3. 计算最小获得 BNB
            path = [token_address, self.WBNB_ADDRESS]
            amounts_out = await self.router.functions.getAmountsOut(sell_amount, path).call()
            expected_bnb_out = amounts_out[-1]
            
            if slippage is None:
                slippage = float(self.config.get("trading", {}).get("slippage", 15))
            
            min_bnb_out = int(expected_bnb_out * (1 - slippage/100) * 0.99)
            
            # 4. 构建卖出交易
            nonce = await self.w3.eth.get_transaction_count(self.account.address)
            
            if gas_price is None:
                gas_price = await self._get_gas_price()
                
            deadline = int(time.time()) + int(self.config.get("trading", {}).get("deadline_seconds", 45))
            
            tx_func = self.router.functions.swapExactTokensForETHSupportingFeeOnTransferTokens(
                sell_amount,
                min_bnb_out,
                path,
                self.account.address,
                deadline
            )
            
            tx_params = {
                'from': self.account.address,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': self.config.get("network", {}).get("chain_id", 56)
            }
            
            # 估算 Gas
            try:
                estimated_gas = await tx_func.estimate_gas(tx_params)
                tx_params['gas'] = int(estimated_gas * 1.2)
            except Exception as e:
                logger.warning(f"卖出估算 Gas 失败: {e}")
                tx_params['gas'] = 500000 # 卖出通常比买入贵 (涉及 transferFrom)
                
            # 5. 发送交易
            tx = await tx_func.build_transaction(tx_params)
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.account.key)
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            tx_hash_hex = self.w3.to_hex(tx_hash)
            
            logger.info(f"卖出交易已发送: {tx_hash_hex}, 等待确认...")
            
            receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            
            if receipt.status == 1:
                logger.success(f"卖出 {token_symbol} 成功!")
                
                # 1. Calculate Actual BNB Received
                raw_bnb_got = self._parse_swap_output_log(receipt)
                
                if raw_bnb_got == 0:
                    # Fallback: Use expected_bnb_out if parsing fails
                    raw_bnb_got = expected_bnb_out
                
                bnb_got = float(self.w3.from_wei(raw_bnb_got, 'ether'))
                expected_bnb_human = float(self.w3.from_wei(expected_bnb_out, 'ether'))
                
                sell_amount_human = float(self.w3.from_wei(sell_amount, 'ether'))
                
                # 2. Calculate Slippage
                slippage_pct = 0.0
                if expected_bnb_human > 0:
                    slippage_pct = ((expected_bnb_human - bnb_got) / expected_bnb_human) * 100
                
                slippage_bnb = expected_bnb_human - bnb_got
                
                # Check alert
                await self._check_slippage_alert(slippage_pct, token_symbol, "sell", tx_hash_hex)
                
                # 3. Gas
                gas_used = receipt['gasUsed']
                effective_gas_price = receipt.get('effectiveGasPrice', gas_price)
                gas_price_gwei = effective_gas_price / 1e9
                gas_cost_bnb = (gas_used * effective_gas_price) / 1e18
                
                total_cost_bnb = slippage_bnb + gas_cost_bnb
                
                # Calculate PnL if cost_basis is provided
                if cost_basis_bnb is not None and cost_basis_bnb > 0:
                    pnl_bnb = bnb_got - cost_basis_bnb
                    pnl_percentage = (pnl_bnb / cost_basis_bnb) * 100
                    
                final_sell_pct = sell_percentage_real if sell_percentage_real is not None else sell_percentage
                
                # 记录数据库
                await self._log_trade(
                    token_address, token_symbol, "sell", 
                    str(sell_amount_human), str(bnb_got), 
                    tx_hash_hex, "success",
                    pnl_bnb=pnl_bnb,
                    pnl_percentage=pnl_percentage,
                    sell_percentage=final_sell_pct,
                    expected_amount=expected_bnb_human,
                    actual_amount=bnb_got,
                    slippage_pct=slippage_pct,
                    slippage_bnb=slippage_bnb,
                    gas_used=gas_used,
                    gas_price_gwei=gas_price_gwei,
                    gas_cost_bnb=gas_cost_bnb,
                    total_cost_bnb=total_cost_bnb
                )
                return {"status": "success", "tx_hash": tx_hash_hex, "amount_bnb": bnb_got}
            else:
                logger.error(f"卖出 {token_symbol} 失败 (Reverted)")
                return {"status": "failed", "reason": "reverted", "tx_hash": tx_hash_hex}

        except Exception as e:
            logger.error(f"卖出执行异常: {e}")
            return {"status": "failed", "reason": str(e)}

    async def _approve_token(self, token_contract, token_address):
        """内部函数：授权代币"""
        try:
            max_uint256 = 2**256 - 1
            nonce = await self.w3.eth.get_transaction_count(self.account.address)
            gas_price = await self._get_gas_price()
            
            tx_func = token_contract.functions.approve(self.ROUTER_ADDRESS, max_uint256)
            
            tx_params = {
                'from': self.account.address,
                'gasPrice': gas_price,
                'nonce': nonce,
                'chainId': self.config.get("network", {}).get("chain_id", 56),
                'gas': 100000
            }
            
            tx = await tx_func.build_transaction(tx_params)
            signed_tx = self.w3.eth.account.sign_transaction(tx, private_key=self.account.key)
            tx_hash = await self.w3.eth.send_raw_transaction(signed_tx.raw_transaction)
            
            logger.info(f"授权交易已发送: {self.w3.to_hex(tx_hash)}, 等待确认...")
            await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
            logger.success("授权成功")
            
        except Exception as e:
            logger.error(f"授权失败: {e}")
            raise e

    async def _get_bnb_price_usd(self):
        """
        获取 BNB 价格 (USD)
        策略:
        1. 优先检查 5 分钟内的有效缓存
        2. 按顺序尝试: Binance -> CoinGecko -> OKX
        3. 失败时使用 10 分钟内的过期缓存
        4. 最终兜底: 600
        """
        async with self.bnb_price_lock:
            current_time = time.time()
            
            # 1. Check Cache (valid for 5 minutes = 300 seconds)
            if current_time - self.last_bnb_price_time < 300:
                return self.last_bnb_price

            # API Sources Configuration
        sources = [
            {
                "name": "Binance",
                "url": "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT",
                "parser": lambda data: float(data.get("price", 0.0))
            },
            {
                "name": "CoinGecko",
                "url": "https://api.coingecko.com/api/v3/simple/price?ids=binancecoin&vs_currencies=usd",
                "parser": lambda data: float(data.get("binancecoin", {}).get("usd", 0.0))
            },
            {
                "name": "OKX",
                "url": "https://www.okx.com/api/v5/market/ticker?instId=BNB-USDT",
                "parser": lambda data: float(data.get("data")[0].get("last", 0.0)) if data.get("data") else 0.0
            }
        ]

        async with aiohttp.ClientSession() as session:
            for source in sources:
                try:
                    async with session.get(source["url"], timeout=3) as response:
                        if response.status == 200:
                            data = await response.json()
                            price = source["parser"](data)
                            if price > 0:
                                self.last_bnb_price = price
                                self.last_bnb_price_time = current_time
                                logger.info(f"BNB Price updated from {source['name']}: ${price}")
                                return price
                except Exception as e:
                    logger.warning(f"{source['name']} 获取 BNB 价格失败: {e}")

        # 3. Fallback: Local Cache (valid for 10 minutes = 600 seconds)
        if current_time - self.last_bnb_price_time < 600:
            logger.warning(f"所有 API 失败，使用 10 分钟内的缓存价格: ${self.last_bnb_price}")
            return self.last_bnb_price

        # 4. Final Fallback: Fixed Value
        logger.warning("所有 BNB 价格来源均失败且缓存过期，使用兜底值 600.0 USD")
        return 600.0


    async def get_token_price(self, token_address):
        """查询代币价格 (以 BNB 和 USD 计价) - 优先使用储备量计算以提高精度"""
        token_address = self.w3_to_checksum(token_address)
        try:
            # 1. 获取 BNB 价格
            bnb_price_usd = await self._get_bnb_price_usd()
            
            # 2. Get Decimals (Cached)
            if token_address in self.token_decimals_cache:
                decimals = self.token_decimals_cache[token_address]
            else:
                token_contract = self.w3.eth.contract(address=token_address, abi=ERC20_ABI)
                decimals = await token_contract.functions.decimals().call()
                self.token_decimals_cache[token_address] = decimals
            
            price_bnb = 0.0
            liquidity_bnb = 0.0
            
            # 3. 尝试通过储备量计算价格 (最准确，无视滑点，解决低价币精度问题)
            try:
                # Get Factory (Cached)
                if not self.factory_address:
                    self.factory_address = await self.router.functions.factory().call()
                
                # Get Pair (Cached)
                if token_address in self.pair_address_cache:
                    pair_address = self.pair_address_cache[token_address]
                else:
                    factory = self.w3.eth.contract(address=self.factory_address, abi=PANCAKESWAP_V2_FACTORY_ABI)
                    pair_address = await factory.functions.getPair(token_address, self.WBNB_ADDRESS).call()
                    self.pair_address_cache[token_address] = pair_address
                
                if pair_address != "0x0000000000000000000000000000000000000000":
                    pair = self.w3.eth.contract(address=pair_address, abi=PANCAKESWAP_PAIR_ABI)
                    reserves = await pair.functions.getReserves().call()
                    
                    # Get Token0 (Cached)
                    if pair_address in self.pair_token0_cache:
                        token0 = self.pair_token0_cache[pair_address]
                    else:
                        token0 = await pair.functions.token0().call()
                        self.pair_token0_cache[pair_address] = token0
                    
                    if token0.lower() == self.WBNB_ADDRESS.lower():
                        reserve_bnb = reserves[0]
                        reserve_token = reserves[1]
                    else:
                        reserve_bnb = reserves[1]
                        reserve_token = reserves[0]
                        
                    # 计算流动性
                    liquidity_bnb = float(self.w3.from_wei(reserve_bnb, 'ether'))
                    
                    # 计算价格: Price = BNB_Reserve / Token_Reserve
                    if reserve_token > 0:
                        # 调整精度
                        adj_reserve_bnb = reserve_bnb / (10 ** 18)
                        adj_reserve_token = reserve_token / (10 ** decimals)
                        if adj_reserve_token > 0:
                            price_bnb = adj_reserve_bnb / adj_reserve_token
            except Exception as e:
                logger.warning(f"储备量价格计算失败: {e}")

            # 4. Fallback: 如果储备量方法失败 (比如没有直接 LP)，使用路由 getAmountsOut
            if price_bnb == 0:
                try:
                    # 反向查询：1 BNB 能买多少 Token，然后倒数
                    one_bnb = self.w3.to_wei(1, 'ether')
                    path = [self.WBNB_ADDRESS, token_address]
                    amounts = await self.router.functions.getAmountsOut(one_bnb, path).call()
                    tokens_got = amounts[-1] / (10 ** decimals)
                    if tokens_got > 0:
                        price_bnb = 1.0 / tokens_got
                except Exception as e:
                     logger.debug(f"路由价格查询失败 (Buy Path): {e}")
                     # 再次 Fallback: 正向查询 (可能精度丢失)
                     try:
                         one_token = 10 ** decimals
                         path = [token_address, self.WBNB_ADDRESS]
                         amounts = await self.router.functions.getAmountsOut(one_token, path).call()
                         price_bnb = float(self.w3.from_wei(amounts[-1], 'ether'))
                     except Exception as e2:
                         logger.debug(f"路由价格查询失败 (Sell Path): {e2}")

            price_usd = float(price_bnb) * bnb_price_usd

            return {
                "price_bnb": float(price_bnb),
                "price_usd": price_usd,
                "liquidity_bnb": liquidity_bnb
            }
        except Exception as e:
            logger.warning(f"查询价格失败: {e}")
            return None

    def _parse_transfer_log(self, receipt, token_address):
        """解析 Transfer 事件获取实际到账数量"""
        try:
            token_address = token_address.lower()
            my_address = self.account.address.lower()
            amount = 0
            
            # Transfer Event Signature
            TRANSFER_TOPIC = "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef"
            
            for log in receipt['logs']:
                if log['address'].lower() == token_address:
                    if len(log['topics']) > 0 and log['topics'][0].hex().lower() == TRANSFER_TOPIC:
                        # topic[1] is from, topic[2] is to. Topics are 32 bytes.
                        if len(log['topics']) >= 3:
                            # Extract address from last 20 bytes
                            to_addr = '0x' + log['topics'][2].hex()[-40:]
                            if to_addr.lower() == my_address:
                                # value is in data
                                try:
                                    val = int(log['data'].hex(), 16)
                                    amount += val
                                except:
                                    pass
            return amount
        except Exception as e:
            logger.error(f"解析 Transfer Log 失败: {e}")
            return 0

    def _parse_swap_output_log(self, receipt):
        """解析 Swap 事件获取实际 BNB (WBNB) 输出"""
        try:
            # Swap Event Signature
            SWAP_TOPIC = "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
            wbnb_addr = self.WBNB_ADDRESS.lower()
            total_out = 0
            
            for log in receipt['logs']:
                if len(log['topics']) > 0 and log['topics'][0].hex().lower() == SWAP_TOPIC:
                    # Check if this pair involves WBNB
                    # We can't easily check pair address without caching, but we can check if the output "makes sense"
                    # Ideally we check if the log emitter is a pair with WBNB.
                    # Simplified: Just look at the data. Swap data: amount0In, amount1In, amount0Out, amount1Out
                    try:
                        data_hex = log['data'].hex()
                        # 4 params * 32 bytes = 128 bytes (256 hex chars)
                        if len(data_hex) >= 256:
                            # amount0Out is at [64:128], amount1Out is at [192:256] (indices in hex chars)
                            # Actually:
                            # 0: amount0In
                            # 1: amount1In
                            # 2: amount0Out
                            # 3: amount1Out
                            # Each is 64 hex chars
                            
                            chunk_size = 64
                            amount0Out = int(data_hex[2*chunk_size:3*chunk_size], 16)
                            amount1Out = int(data_hex[3*chunk_size:4*chunk_size], 16)
                            
                            # We don't know which one is WBNB without checking token0/token1 of the pair.
                            # But usually we are selling Token -> WBNB.
                            # So we expect one of the Outs to be significant and roughly match our expectation.
                            # However, to be precise, we should probably rely on the fact that we sold X tokens.
                            # Let's assume the one that is NOT the token we sold is WBNB?
                            # Or better: check if we can match the pair address?
                            # Too complex for now.
                            
                            # Alternative strategy: Look for Deposit/Withdraw on WBNB contract?
                            # When swapping Token -> ETH, the router receives WBNB then withdraws.
                            # So there should be a Withdrawal event from WBNB address.
                            pass
                    except:
                        pass
            
            # Strategy 2: Check Withdrawal event from WBNB contract
            # Withdrawal(address indexed src, uint256 wad)
            WITHDRAWAL_TOPIC = "0x7fcf532c15f0a6db0bd6d0e038bea71d30d808c7d98cb3bf7268a95bf5081b65"
            for log in receipt['logs']:
                if log['address'].lower() == wbnb_addr:
                    if len(log['topics']) > 0 and log['topics'][0].hex().lower() == WITHDRAWAL_TOPIC:
                        # wad is in data
                        try:
                            val = int(log['data'].hex(), 16)
                            total_out += val
                        except:
                            pass
            
            return total_out
        except Exception as e:
            logger.error(f"解析 Swap/Withdraw Log 失败: {e}")
            return 0


    async def _send_telegram_alert(self, message):
        """发送 Telegram 告警"""
        try:
            tg_conf = self.config.get("notifications", {})
            # Check env var for enable override? No, stick to config + env for creds
            if not tg_conf.get("enable_telegram", False):
                return
            
            token = tg_conf.get("telegram_token") or os.getenv("TELEGRAM_TOKEN")
            chat_id = tg_conf.get("telegram_chat_id") or os.getenv("TELEGRAM_CHAT_ID")
            
            if not token or not chat_id:
                return
                
            url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": message,
                "parse_mode": "Markdown"
            }
            
            async with aiohttp.ClientSession() as session:
                async with session.post(url, json=payload, timeout=10) as response:
                    if response.status != 200:
                        logger.error(f"Telegram 发送失败: {await response.text()}")
                        
        except Exception as e:
            logger.error(f"Telegram 告警异常: {e}")

    async def _check_slippage_alert(self, slippage_pct, token_symbol, action, tx_hash):
        """检查滑点是否异常并发送告警"""
        try:
            config_slippage = float(self.config.get("trading", {}).get("slippage", 12))
            # Rule: > Config + 5%
            threshold = config_slippage + 5.0
            
            if slippage_pct > threshold:
                msg = (
                    f"⚠️ *滑点异常告警*\n"
                    f"币种: `{token_symbol}`\n"
                    f"动作: {action.upper()}\n"
                    f"设定滑点: {config_slippage}%\n"
                    f"实际滑点: {slippage_pct:.2f}%\n"
                    f"阈值: {threshold}%\n"
                    f"[查看交易](https://bscscan.com/tx/{tx_hash})"
                )
                logger.warning(f"触发滑点告警: {slippage_pct}% > {threshold}%")
                asyncio.create_task(self._send_telegram_alert(msg))
        except Exception as e:
            logger.error(f"滑点检查失败: {e}")

    async def _log_trade(self, token_addr, token_name, action, amount_token, amount_bnb, tx_hash, status, price_bnb=None, token_symbol=None, pnl_bnb=0.0, pnl_percentage=0.0,
                         expected_amount=0.0, actual_amount=0.0, slippage_pct=0.0, slippage_bnb=0.0,
                         gas_used=0, gas_price_gwei=0.0, gas_cost_bnb=0.0, total_cost_bnb=0.0, sell_percentage=100.0):
        """记录交易到数据库"""
        try:
            # Try to calculate price_bnb if not provided
            if price_bnb is None and amount_token and amount_bnb:
                try:
                    amt_t = float(amount_token)
                    amt_b = float(amount_bnb)
                    if amt_t > 0:
                        price_bnb = str(amt_b / amt_t)
                except:
                    pass

            # Use token_name as symbol if symbol not provided (backward compatibility)
            if token_symbol is None:
                token_symbol = token_name
                
            # Ensure token_name is valid (fix for '$' or empty)
            safe_token_name = token_name
            if not safe_token_name or safe_token_name == '$':
                 safe_token_name = token_symbol or token_addr[:8]

            async with aiosqlite.connect(self.db_path) as db:
                # Get current timestamp
                timestamp = datetime.datetime.now().isoformat()
                
                # Schema: id, token_address, token_name, token_symbol, action, amount, price, tx_hash, status, timestamp, pnl_percentage, pnl_bnb + slippage cols
                await db.execute(f"""
                    INSERT INTO {self.trades_table} (
                        token_address, token_name, token_symbol, action, amount_token, amount_bnb, price_bnb, 
                        tx_hash, status, created_at, pnl_bnb, pnl_percentage, sell_percentage,
                        expected_amount, actual_amount, slippage_pct, slippage_bnb,
                        gas_used, gas_price_gwei, gas_cost_bnb, total_cost_bnb
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    token_addr, safe_token_name, token_symbol, action, float(amount_token), float(amount_bnb) if amount_bnb else 0.0, float(price_bnb) if price_bnb else 0.0, 
                    tx_hash, status, timestamp, pnl_bnb, pnl_percentage, float(sell_percentage),
                    float(expected_amount), float(actual_amount), float(slippage_pct), float(slippage_bnb),
                    int(gas_used), float(gas_price_gwei), float(gas_cost_bnb), float(total_cost_bnb)
                ))
                await db.commit()
        except Exception as e:
            logger.error(f"数据库写入失败: {e}")

    async def close(self):
        """关闭连接"""
        # AsyncWeb3 不需要显式关闭，但如果有 Session 可以关闭
        pass

# 简单测试入口
if __name__ == "__main__":
    async def main():
        # 需要在 .env 配置真实私钥才能运行
        try:
            executor = BSCExecutor()
            await executor.init_executor()
            print("初始化成功，钱包:", executor.account.address)
            # await executor.buy_token("0x...", "TEST")
        except Exception as e:
            print(f"初始化失败: {e}")

    asyncio.run(main())
