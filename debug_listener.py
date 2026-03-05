import asyncio
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

# PancakeSwap Factory V2
FACTORY_ADDRESS = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"

RPCS = [
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bscrpc.com",
    "https://1rpc.io/bnb",
    "https://bsc-dataseed1.defibit.io",
]

async def main():
    w3 = None
    connected_rpc = None
    
    for rpc_url in RPCS:
        print(f"Trying {rpc_url}...")
        try:
            temp_w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url, request_kwargs={'timeout': 10}))
            temp_w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if await temp_w3.is_connected():
                print(f"Connected to {rpc_url}")
                w3 = temp_w3
                connected_rpc = rpc_url
                break
            else:
                print(f"Failed to connect to {rpc_url}")
        except Exception as e:
            print(f"Error connecting to {rpc_url}: {e}")

    if not w3:
        print("Could not connect to any RPC")
        return
    
    try:
        latest_block = await w3.eth.block_number
        print(f"Latest block: {latest_block}")
        
        from_block = latest_block - 50
        to_block = latest_block
        
        print(f"Querying logs from {from_block} to {to_block}...")
        
        topic0 = "0x0d3648bd0f6ba80134a33ba9275ac585d9d315f0ad8355cddefde31afa28d0e9" # PairCreated
        
        filter_params = {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": FACTORY_ADDRESS,
            "topics": [topic0]
        }
        
        # Test: w3.eth.get_logs
        logs = await w3.eth.get_logs(filter_params)
        print(f"Success! Found {len(logs)} logs.")
        for log in logs:
            print(f"Log Tx: {log['transactionHash'].hex()}")
            break 
            
    except Exception as e:
        print(f"Error during execution: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
