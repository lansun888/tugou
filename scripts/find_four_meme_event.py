import asyncio
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

# BSC RPC List
RPC_URLS = [
    "https://bsc-dataseed1.binance.org",
    "https://bsc-dataseed2.binance.org",
    "https://bscrpc.com",
    "https://1rpc.io/bnb",
    "https://bsc-dataseed1.defibit.io",
]
FACTORY_ADDRESS = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
TOKEN_ADDRESS = "0x2aebb93d8314219fcc0ac4b95227024a31dd4444"

async def find_creation_event():
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

    print(f"Searching for creation event of {TOKEN_ADDRESS} from factory {FACTORY_ADDRESS}")
    
    token_address_clean = TOKEN_ADDRESS.lower()
    # Remove 0x prefix for topic matching if needed, but usually we convert to bytes or pad.
    # Topic is 32 bytes.
    # We need to construct the topic correctly.
    # Address is 20 bytes. Padded to 32 bytes: 12 bytes of zeros + 20 bytes of address.
    token_topic_hex = "0x000000000000000000000000" + token_address_clean[2:]
    token_topic_bytes = bytes.fromhex(token_topic_hex[2:])
    
    current_block = await w3.eth.block_number
    print(f"Current block: {current_block}")
    
    # Search last 5000 blocks for ANY event from factory
    BLOCKS_TO_SEARCH = 5000
    
    start_block = current_block - BLOCKS_TO_SEARCH
    if start_block < 0: start_block = 0
    
    print(f"Searching for ANY events from factory {factory_address} from block {start_block} to {current_block}...")
    
    factory_address = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
    
    chunk_size = 1000
    found_count = 0
    
    for i in range(start_block, current_block + 1, chunk_size):
        end = min(i + chunk_size - 1, current_block)
        print(f"Checking {i} to {end}...")
        
        try:
            # No topics filter - get all events
            logs = await w3.eth.get_logs({
                "fromBlock": i,
                "toBlock": end,
                "address": factory_address
            })
            
            if logs:
                print(f"Found {len(logs)} events in this chunk!")
                for log in logs:
                    found_count += 1
                    tx_hash = log['transactionHash'].hex()
                    block_number = log['blockNumber']
                    topics = [t.hex() for t in log['topics']]
                    
                    print(f"Event in block {block_number}, Tx: {tx_hash}")
                    print(f"Topics: {topics}")
                    
                    # Check if our calculated topic is among them
                    if "0xef0c04052959ad172ea72063a1012a3986aa06f24a6f4c41eb46103b9583390c" in topics:
                        print("MATCHES TokenCreate(address,address) topic!")
                    
        except Exception as e:
            print(f"Error fetching logs: {e}")
            await asyncio.sleep(1)
            continue

    if found_count == 0:
        print("No events found in the last 5,000 blocks.")
    else:
        print(f"Total events found: {found_count}")

    await w3.provider.disconnect()

if __name__ == "__main__":
    asyncio.run(find_creation_event())
