import asyncio
import logging
import sys
import os
from web3 import AsyncWeb3

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Configure logging before imports
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    stream=sys.stdout
)
logger = logging.getLogger(__name__)

from bsc_bot.monitor.four_meme_graduation_listener import FourMemeGraduationListener

async def main():
    # Setup Web3
    rpc_urls = [
        "https://bsc-rpc.publicnode.com",
        "https://binance.llamarpc.com",
        "https://bsc-dataseed1.binance.org/",
        "https://binance.ankr.com/bsc",
        "https://1rpc.io/bnb"
    ]
    
    proxy_url = "http://127.0.0.1:10809" # From config.yaml
    
    w3 = None
    # Try direct first, then proxy
    modes = [{}, {'proxy': proxy_url}]
    
    for mode in modes:
        if w3: break
        request_kwargs = {'timeout': 10}
        request_kwargs.update(mode)
        
        for rpc_url in rpc_urls:
            logger.info(f"Trying RPC: {rpc_url} (proxy={mode.get('proxy', 'None')})")
            try:
                provider = AsyncWeb3.AsyncHTTPProvider(rpc_url, request_kwargs=request_kwargs)
                temp_w3 = AsyncWeb3(provider)
                if await temp_w3.is_connected():
                    logger.info(f"Connected to RPC: {rpc_url}")
                    w3 = temp_w3
                    break
                else:
                    logger.warning(f"Failed to connect to {rpc_url}")
            except Exception as e:
                logger.warning(f"Error connecting to {rpc_url}: {e}")
            
    if not w3:
        logger.error("Failed to connect to any RPC")
        return
    
    # Initialize Listener
    listener = FourMemeGraduationListener(w3)
    
    # Start Listener
    logger.info("Starting FourMemeGraduationListener...")
    task = asyncio.create_task(listener.start())
    
    try:
        # Run for 30 seconds
        for i in range(30):
            await asyncio.sleep(1)
            if i % 5 == 0:
                logger.info(f"Running... {i}s")
    except KeyboardInterrupt:
        pass
    finally:
        logger.info("Stopping listener...")
        await listener.stop()
        try:
            await asyncio.wait_for(task, timeout=5.0)
        except Exception as e:
            logger.error(f"Error stopping task: {e}")

if __name__ == "__main__":
    asyncio.run(main())
