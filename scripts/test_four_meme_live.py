import asyncio
import logging
import sys
import os

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bsc_bot.monitor.pair_listener import PairListener

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def main():
    print("Initializing Live Monitor Test for Four.Meme...")
    # Initialize with dummy path, we will inject config manually
    listener = PairListener(config_path="dummy_config.yaml")
    
    # Inject minimal config
    listener.config = {
        "network": {
            "private_rpcs": ["https://bsc-dataseed.binance.org/", "https://1rpc.io/bnb"], # Force use of public RPCs
            "polling_interval": 1.0
        },
        "monitor": {
            "dex_enabled": {
                "pancakeswap_v2": False,
                "pancakeswap_v3": False,
                "biswap": False,
                "four_meme": True
            },
            "observation_wait_time": 0,
            "min_liquidity_bnb": 0,
            "competition_threshold": 3
        }
    }
    
    print("Starting listener for 30 seconds...")
    print("Using public RPCs (might be slow or rate-limited)...")
    
    # Run in background
    task = asyncio.create_task(listener.run())
    
    try:
        # Wait for 30 seconds
        for i in range(30):
            if not listener.running:
                break
            await asyncio.sleep(1)
            if i % 5 == 0:
                print(f"Running... {i}s")
    except KeyboardInterrupt:
        pass
    finally:
        print("Stopping listener...")
        listener.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except asyncio.TimeoutError:
            print("Task cancellation timed out")
        except Exception as e:
            print(f"Task exception: {e}")

if __name__ == "__main__":
    asyncio.run(main())
