import asyncio
import os
import sys
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load env
load_dotenv()

RPC_URL = "https://bsc-rpc.publicnode.com"

async def analyze_tx():
    print(f"Connecting to {RPC_URL}...")
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    
    tx_hash = "0x497c578c4deaae74ffd40e17d417508d3a7252374acf2e660b45f54fd4f02445"
    
    print(f"Fetching Tx: {tx_hash}")
    tx = await w3.eth.get_transaction(tx_hash)
    print(f"To: {tx['to']}")
    print(f"Input: {tx['input'].hex()[:64]}...")
    
    print(f"\nFetching Receipt...")
    receipt = await w3.eth.get_transaction_receipt(tx_hash)
    
    for i, log in enumerate(receipt['logs']):
        print(f"\nLog #{i} (Address: {log['address']})")
        print(f"Topics: {[t.hex() for t in log['topics']]}")
        print(f"Data: {log['data'].hex()}")
        
        # Try to find potential addresses in data
        data_hex = log['data'].hex()[2:] # remove 0x
        # Split into 32-byte words (64 hex chars)
        words = [data_hex[i:i+64] for i in range(0, len(data_hex), 64)]
        
        print("Data Words:")
        for j, word in enumerate(words):
            # Check if it looks like an address (starts with 24 zeros)
            is_addr = word.startswith("000000000000000000000000")
            marker = " [Potential Address]" if is_addr else ""
            print(f"  [{j}] {word}{marker}")

if __name__ == "__main__":
    asyncio.run(analyze_tx())
