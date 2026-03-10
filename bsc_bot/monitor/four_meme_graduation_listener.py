import asyncio
import logging
import aiosqlite
from web3 import Web3, AsyncWeb3
from datetime import datetime

logger = logging.getLogger(__name__)

PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

PAIR_CREATED_ABI = [{
    "anonymous": False,
    "inputs": [
        {"indexed": True, "name": "token0", "type": "address"},
        {"indexed": True, "name": "token1", "type": "address"},
        {"indexed": False, "name": "pair", "type": "address"},
        {"indexed": False, "name": "allPairsLength", "type": "uint256"}
    ],
    "name": "PairCreated",
    "type": "event"
}]

ERC20_ABI = [
    {
        "inputs": [],
        "name": "name",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "stateMutability": "view",
        "type": "function"
    },
    {
        "inputs": [{"name": "account", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "", "type": "uint256"}],
        "stateMutability": "view",
        "type": "function"
    }
]

PAIR_ABI = [
    {
        "inputs": [],
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
        "inputs": [],
        "name": "token0",
        "outputs": [{"name": "", "type": "address"}],
        "stateMutability": "view",
        "type": "function"
    }
]

class FourMemeGraduationListener:

    POLL_INTERVAL = 3       # 每3秒轮询一次
    BLOCKS_PER_POLL = 3     # 每次查最近3个块

    def __init__(self, w3, db_path, bot, config):
        self.w3 = w3
        self.db_path = db_path
        self.bot = bot
        self.config = config
        self.proxy = config.get('proxy',{}).get('http','')
        self._processing = set()
        self._last_block = None
        self.db = None
        
        # 计算PairCreated事件签名
        self.PAIR_CREATED_TOPIC = Web3.to_hex(
            Web3.keccak(text="PairCreated(address,address,address,uint256)")
        )

    async def run(self):
        logger.info("FourMemeGraduationListener 启动 (HTTP轮询模式)")
        
        # 获取当前最新块号作为起点
        try:
            self._last_block = await self.w3.eth.block_number
        except Exception as e:
            logger.error(f"获取起始块号失败: {e}")
            self._last_block = 0

        logger.info(f"起始块号: {self._last_block}")
        
        # 建立数据库连接
        async with aiosqlite.connect(self.db_path) as db:
            self.db = db
            while True:
                try:
                    await self._poll_new_pairs()
                except Exception as e:
                    logger.error(f"轮询异常: {e}")
                
                await asyncio.sleep(self.POLL_INTERVAL)

    async def _poll_new_pairs(self):
        """
        用HTTP查询最新块的PairCreated事件
        """
        try:
            current_block = await self.w3.eth.block_number
            
            if self._last_block is None:
                 self._last_block = current_block - 1

            if current_block <= self._last_block:
                return
            
            from_block = self._last_block + 1
            to_block = current_block
            
            # 防止查询范围太大
            if to_block - from_block > 10:
                from_block = to_block - 10
            
            logger.debug(
                f"查询块 {from_block}-{to_block}"
            )
            
            # 查询PairCreated事件
            filter_params = {
                "address": PANCAKE_FACTORY,
                "topics": [self.PAIR_CREATED_TOPIC],
                "fromBlock": hex(from_block),
                "toBlock": hex(to_block)
            }
            logger.debug(f"get_logs filter: {filter_params}")
            
            logs = await self.w3.eth.get_logs(filter_params)
            
            if logs:
                logger.info(
                    f"发现 {len(logs)} 个新交易对 "
                    f"(块 {from_block}-{to_block})"
                )
            
            for log in logs:
                asyncio.create_task(
                    self._handle_pair_created(log)
                )
            
            self._last_block = current_block
            
        except Exception as e:
            logger.warning(f"轮询查询失败: {e}")

    async def _handle_pair_created(self, log):
        """处理PairCreated事件 - 所有WBNB交易对都走安全检测"""
        try:
            topics = log.get("topics", [])
            if len(topics) < 3:
                return

            token0 = "0x" + topics[1].hex()[-40:]
            token1 = "0x" + topics[2].hex()[-40:]

            data = log.get("data", b"")
            data_hex = data.hex() if isinstance(
                data, bytes
            ) else str(data).replace("0x", "")

            if len(data_hex) < 64:
                return
            pair = "0x" + data_hex[24:64]

            # 找非WBNB代币
            if token0.lower() == WBNB.lower():
                token_address = token1
            elif token1.lower() == WBNB.lower():
                token_address = token0
            else:
                return

            # 防重复
            if token_address in self._processing:
                return
            self._processing.add(token_address)

            logger.info(
                f"新交易对: {token_address[:10]} "
                f"pair={pair[:10]}"
            )

            try:
                await self._process_new_listing(
                    token_address, pair
                )
            finally:
                self._processing.discard(token_address)

        except Exception as e:
            logger.error(
                f"处理失败: {e}", exc_info=True
            )

    async def _process_new_listing(
        self, token_address, pair_address
    ):
        try:
            # 读名称，超时3秒
            token_name, token_symbol = \
                await asyncio.wait_for(
                    self._get_token_info(token_address),
                    timeout=3.0
                )
            logger.info(
                f"代币名称: {token_name}({token_symbol})"
            )

            # 读流动性，超时3秒
            liquidity_bnb = await asyncio.wait_for(
                self._get_liquidity_bnb(
                    pair_address, token_address
                ),
                timeout=3.0
            )
            logger.info(f"流动性: {liquidity_bnb:.2f}BNB")

            if liquidity_bnb < 5:
                logger.info(
                    f"流动性不足跳过: "
                    f"{token_name} {liquidity_bnb:.2f}BNB"
                )
                return

            logger.info(
                f"✅ 进入买入流程: {token_name}"
            )

            await self.bot.process_single_pair({
                'token': {
                    'address': token_address,
                    'symbol': token_symbol,
                },
                'deployer': '0x' + '0' * 40,
                'pair': pair_address,
                'dex': 'four_meme',
                'liquidity_bnb': liquidity_bnb,
                'source': 'graduation',
            })

        except asyncio.TimeoutError:
            logger.warning(
                f"处理超时: {token_address[:10]}"
            )
        except Exception as e:
            logger.error(
                f"处理异常: {token_address[:10]} {e}",
                exc_info=True
            )

    async def _get_token_info(self, token_address) -> tuple:
        """从链上读取代币名称和symbol"""
        try:
            contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(token_address),
                abi=ERC20_ABI
            )
            name = await contract.functions.name().call()
            symbol = await contract.functions.symbol().call()
            return name, symbol
        except Exception as e:
            logger.warning(f"名称读取失败 {token_address[:10]}: {e}")
            return token_address[:8], "UNKNOWN"

    async def _get_liquidity_bnb(self, pair_address, token_address) -> float:
        """从pair合约读取BNB流动性"""
        try:
            pair_contract = self.w3.eth.contract(
                address=Web3.to_checksum_address(pair_address),
                abi=PAIR_ABI
            )

            reserves = await pair_contract.functions.getReserves().call()
            token0 = await pair_contract.functions.token0().call()

            # 判断哪个是BNB储备
            if token0.lower() == WBNB.lower():
                bnb_reserve = reserves[0]
            else:
                bnb_reserve = reserves[1]

            return bnb_reserve / 1e18

        except Exception as e:
            logger.warning(f"流动性读取失败 {pair_address[:10]}: {e}")
            return 0.0
