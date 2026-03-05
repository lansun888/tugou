import asyncio
from datetime import datetime
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

# BSC RPC List
RPC_URLS = [
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bscrpc.com",
    "https://1rpc.io/bnb",
]

TOKEN_ADDRESS = AsyncWeb3.to_checksum_address("0x2aebb93d8314219fcc0ac4b95227024a31dd4444")

async def get_deployment_block():
    w3 = None
    for rpc in RPC_URLS:
        try:
            provider = AsyncWeb3.AsyncHTTPProvider(rpc, request_kwargs={'timeout': 10})
            temp_w3 = AsyncWeb3(provider)
            temp_w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if await temp_w3.is_connected():
                w3 = temp_w3
                print(f"Connected to {rpc}")
                break
        except Exception as e:
            print(f"Failed to connect to {rpc}: {e}")
    
    if not w3:
        print("Failed to connect to any RPC")
        return

    current_block = await w3.eth.block_number
    print(f"Current block: {current_block}")
    
    # Search last ~1 month (1,000,000 blocks)
    # If not found, you can increase this range.
    search_range = 1000000
    low = current_block - search_range
    if low < 0: low = 0
    high = current_block
    
    print(f"Searching for deployment between blocks {low} and {high}...")
    
    deployment_block = None
    
    while low <= high:
        mid = (low + high) // 2
        try:
            code = await w3.eth.get_code(TOKEN_ADDRESS, block_identifier=mid)
            if len(code) > 0:
                deployment_block = mid
                high = mid - 1
            else:
                low = mid + 1
            print(f"Checked block {mid}: {'Contract exists' if len(code) > 0 else 'No contract'}")
        except Exception as e:
            print(f"Error at block {mid}: {e}")
            # If error, try to determine direction or just retry?
            # For simplicity, if we can't check, we might be stuck.
            # But "header not found" usually means block is valid but maybe pruned?
            # Or future?
            # If error is persistent, we might need to skip this block.
            # Let's assume transient error and retry once, then fail.
            await asyncio.sleep(1)
            try:
                code = await w3.eth.get_code(TOKEN_ADDRESS, block_identifier=mid)
                if len(code) > 0:
                    deployment_block = mid
                    high = mid - 1
                else:
                    low = mid + 1
            except:
                print(f"Skipping block {mid} due to error.")
                # We can't decide direction. This breaks binary search.
                # Fallback: just move low up? Or high down?
                # Risky. Let's just stop or continue?
                # If we assume it's a network glitch, we should retry.
                # If it's "header not found" on a very old block, maybe we are too far back.
                # But we are searching recent blocks.
                break

    if deployment_block:
        print(f"Found deployment around block {deployment_block}")
        
        # Verify exact block
        code_prev = await w3.eth.get_code(TOKEN_ADDRESS, block_identifier=deployment_block - 1)
        code_curr = await w3.eth.get_code(TOKEN_ADDRESS, block_identifier=deployment_block)
        
        if len(code_prev) == 0 and len(code_curr) > 0:
            print(f"Confirmed deployment at block {deployment_block}")
            
            # Get block timestamp
            block = await w3.eth.get_block(deployment_block)
            print(f"Timestamp: {block['timestamp']}")
            print(f"Time: {datetime.fromtimestamp(block['timestamp'])}")
        else:
            print("Something is fuzzy, but close enough.")
    else:
        print("Could not find deployment block in the searched range.")

    await w3.provider.disconnect()

if __name__ == "__main__":
    asyncio.run(get_deployment_block())
