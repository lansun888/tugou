import asyncio
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

# Use reliable RPCs
RPC_URLS = [
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bsc-dataseed3.binance.org",
]
FACTORY_ADDRESS = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"

async def dump_logs():
    w3 = None
    for rpc in RPC_URLS:
        try:
            print(f"Connecting to {rpc}...")
            provider = AsyncWeb3.AsyncHTTPProvider(rpc, request_kwargs={'timeout': 10})
            temp_w3 = AsyncWeb3(provider)
            temp_w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if await temp_w3.is_connected():
                w3 = temp_w3
                print(f"Connected to {rpc}")
                break
        except Exception as e:
            print(f"Failed to connect: {e}")
            
    if not w3:
        print("Failed to connect to any RPC")
        return

    current_block = await w3.eth.block_number
    print(f"Current block: {current_block}")
    
    # Check last 100 blocks
    from_block = current_block - 100
    to_block = current_block
    
    print(f"Fetching logs from {from_block} to {to_block} for {FACTORY_ADDRESS}...")
    
    try:
        logs = await w3.eth.get_logs({
            "address": FACTORY_ADDRESS,
            "fromBlock": from_block,
            "toBlock": to_block
        })
        
        print(f"Found {len(logs)} logs.")
        
        # Group by topic0 to see unique events
        events = {}
        for log in logs:
            if not log['topics']: continue
            topic0 = log['topics'][0].hex()
            if topic0 not in events:
                events[topic0] = []
            events[topic0].append(log)
            
        print("\nUnique Event Signatures (Topic0):")
        for t0, logs_list in events.items():
            print(f"\nTopic0: {t0}")
            print(f"Count: {len(logs_list)}")
            # Print first log details
            l = logs_list[0]
            print(f"Topics: {[t.hex() for t in l['topics']]}")
            print(f"Data length: {len(l['data'])}")
            print(f"Data: {l['data'].hex()[:64]}...") # First 32 bytes
            print(f"Tx: {l['transactionHash'].hex()}")
            
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(dump_logs())
