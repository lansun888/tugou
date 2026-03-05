import asyncio
import logging
import sys
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from bsc_bot.analyzer.local_simulator import LocalSimulator

# Configure Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Constants
RPCS = [
    "https://bsc-dataseed.binance.org",
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://binance.llamarpc.com",
    "https://rpc.ankr.com/bsc",
    "https://1rpc.io/bnb",
]
BUSD_ADDRESS = "0xe9e7CEA3DedcA5984780Bafc599bD69ADd087D56"
WBNB_ADDRESS = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
PAIR_ADDRESS = "0x58F876857a02D6762E0101bb5C4618dcB6CE97db" # BUSD-WBNB

async def get_w3():
    for rpc in RPCS:
        logger.info(f"Trying {rpc}...")
        try:
            w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc, request_kwargs={'timeout': 60}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if await w3.is_connected():
                logger.info(f"Connected to {rpc}")
                bn = await w3.eth.block_number
                logger.info(f"Current Block: {bn}")
                return w3
        except Exception as e:
            logger.error(f"Failed to connect to {rpc}: {e}")
            # Ensure session is closed if created? w3.provider doesn't expose session easily directly unless we manage it.
            # But we can ignore for now.
    return None

async def main():
    logger.info("Connecting to RPC...")
    w3 = await get_w3()
    
    if not w3:
        logger.error("Failed to connect to any RPC")
        return

    simulator = LocalSimulator(w3)
    
    logger.info(f"Simulating trade for BUSD ({BUSD_ADDRESS}) on Pair {PAIR_ADDRESS}...")
    
    # 1. Test Slot Finding
    logger.info("Finding Balance Slot...")
    bal_slot = await simulator.find_balance_slot(BUSD_ADDRESS, PAIR_ADDRESS)
    logger.info(f"Balance Slot: {bal_slot}")
    
    logger.info("Finding Allowance Slot...")
    allow_slot = await simulator.find_allowance_slot(BUSD_ADDRESS)
    logger.info(f"Allowance Slot: {allow_slot}")
    
    if bal_slot == -1 or allow_slot == -1:
        logger.error("Failed to find slots, simulation might fail.")
    
    # 2. Simulate Trade
    logger.info("Running Simulation...")
    # Simulate trade: BUSD -> WBNB (Wait, simulate_trade takes token_address and simulates BUY and SELL)
    # Buy: BNB -> BUSD
    # Sell: BUSD -> BNB
    is_hp, b_tax, s_tax, reason = await simulator.simulate_trade(BUSD_ADDRESS, PAIR_ADDRESS, amount_bnb=0.1)
    
    logger.info(f"Simulation Result:")
    logger.info(f"Is Honeypot: {is_hp}")
    logger.info(f"Buy Tax: {b_tax}")
    logger.info(f"Sell Tax: {s_tax}")
    logger.info(f"Reason: {reason}")
    
    if not is_hp:
        logger.info("✅ Simulation PASSED (As expected for BUSD)")
    else:
        logger.error(f"❌ Simulation FAILED: {reason}")

if __name__ == "__main__":
    asyncio.run(main())
