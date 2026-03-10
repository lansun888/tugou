import asyncio
import json
import logging
import time
from web3 import Web3
from bsc_bot.monitor.abis import PANCAKESWAP_V2_FACTORY_ABI, PANCAKESWAP_PAIR_ABI
from bsc_bot.monitor.pair_listener import PairListener

logger = logging.getLogger(__name__)

# Constants
PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

class FourMemeGraduationListener(PairListener):
    def __init__(self, w3, config_path="config.yaml", db_path="./data/bsc_bot.db", loop=None):
        super().__init__(config_path, db_path)
        self.w3 = w3
        self.loop = loop or asyncio.get_event_loop()
        self.running = False
        self.PAIR_CREATED_TOPIC = Web3.to_hex(Web3.keccak(text="PairCreated(address,address,address,uint256)"))
        self._last_block = 0

    async def start(self):
        """Start listening for new pairs via HTTP polling"""
        self.running = True
        logger.info("Four.meme listener started (HTTP Polling Mode)")
        
        # Get current block
        try:
            self._last_block = await self.w3.eth.block_number
            logger.info(f"Start block: {self._last_block}")
        except Exception as e:
            logger.error(f"Failed to get start block: {e}")
            return

        while self.running:
            try:
                await self._poll_new_pairs()
            except Exception as e:
                logger.error(f"Polling error: {e}")
            
            await asyncio.sleep(3)

    async def stop(self):
        self.running = False
        logger.info("Four.meme listener stopped")

    async def _poll_new_pairs(self):
        try:
            current_block = await self.w3.eth.block_number
            if current_block <= self._last_block:
                return

            # Max range 10 blocks to avoid node errors
            from_block = self._last_block + 1
            to_block = current_block
            if to_block - from_block > 10:
                from_block = to_block - 10
            
            logger.debug(f"Querying blocks {from_block}-{to_block}")
            
            logs = await self.w3.eth.get_logs({
                "address": PANCAKE_FACTORY,
                "topics": [self.PAIR_CREATED_TOPIC],
                "fromBlock": from_block,
                "toBlock": to_block
            })

            if logs:
                logger.info(f"Found {len(logs)} new pairs (blocks {from_block}-{to_block})")

            for log in logs:
                asyncio.create_task(self._handle_pair_created(log))

            self._last_block = current_block

        except Exception as e:
            logger.error(f"Error polling new pairs: {e}")

    async def _handle_pair_created(self, log):
        try:
            topics = log.get("topics", [])
            logger.info(f"Processing PairCreated: topics count={len(topics)}")
            
            if len(topics) < 3:
                logger.warning(f"Not enough topics: {topics}")
                return

            # Extract token addresses (topics are 32 bytes, take last 20 bytes)
            token0 = "0x" + topics[1].hex()[-40:]
            token1 = "0x" + topics[2].hex()[-40:]
            
            logger.info(f"token0={token0[:10]}... token1={token1[:10]}...")

            # Extract pair address from data
            data = log.get("data", b"")
            if isinstance(data, bytes):
                data_hex = data.hex()
            else:
                data_hex = str(data).replace("0x", "")
            
            # Pair address is the first 32 bytes of data (usually)
            # Actually PairCreated(address token0, address token1, address pair, uint)
            # token0 is topic1, token1 is topic2
            # pair is data[0:32] (padded) -> take last 20 bytes
            # length is data[32:64]
            
            if len(data_hex) >= 64:
                pair = "0x" + data_hex[24:64]
            else:
                logger.warning(f"Invalid data length: {len(data_hex)}")
                return

            # Determine which is the token (not WBNB)
            token_address = None
            if token0.lower() == WBNB.lower():
                token_address = token1
            elif token1.lower() == WBNB.lower():
                token_address = token0
            else:
                logger.info(f"Skipping non-WBNB pair: {token0[:10]}/{token1[:10]}")
                return

            logger.info(f"Token Address: {token_address} Suffix: {token_address[-4:]}")

            # Check for Four.meme suffix (4444)
            if not token_address.lower().endswith('4444'):
                logger.info(f"Skipping non-4444 token: {token_address}")
                return

            logger.info(f"🎓 Four.meme Token Detected! {token_address}")
            
            # Here you would typically notify the strategy or trigger a buy
            # For now just log it as per requirements

        except Exception as e:
            logger.error(f"_handle_pair_created exception: {e}", exc_info=True)
