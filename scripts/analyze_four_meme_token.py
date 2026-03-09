import asyncio
import os
import sys
from web3 import AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware
from dotenv import load_dotenv

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bsc_bot.monitor.abis import ERC20_ABI

# Load env
load_dotenv()

RPC_URL = "https://bsc-rpc.publicnode.com"
TOKEN_ADDRESS = "0xcf08d70dbc439ad7a4a6af290287f8a00ff84444"
FACTORY_ADDRESS = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"

async def analyze_token():
    print(f"Connecting to {RPC_URL}...")
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(RPC_URL))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    
    if not await w3.is_connected():
        print("Failed to connect to RPC")
        return

    print("Connected!")
    
    token_checksum = Web3.to_checksum_address(TOKEN_ADDRESS)
    contract = w3.eth.contract(address=token_checksum, abi=ERC20_ABI)
    
    try:
        name = await contract.functions.name().call()
        symbol = await contract.functions.symbol().call()
        decimals = await contract.functions.decimals().call()
        total_supply = await contract.functions.totalSupply().call()
        
        print(f"Token: {token_checksum}")
        print(f"Name: {name}")
        print(f"Symbol: {symbol}")
        print(f"Decimals: {decimals}")
        print(f"Total Supply: {total_supply / 10**decimals}")
        
        # Check owner
        try:
            owner = await contract.functions.owner().call()
            print(f"Owner: {owner}")
        except:
            print("Owner: Not found or public")
            
    except Exception as e:
        print(f"Failed to get token info: {e}")

    # Check code size
    code = await w3.eth.get_code(token_checksum)
    print(f"Code Size: {len(code)} bytes")
    
    # Check if it looks like a proxy (minimal proxy pattern)
    if len(code) < 100:
        print("Warning: Code size is very small, might be a proxy or uninitialized.")
    
    # Try to find the creation event (scan last 10000 blocks just in case it's recent, otherwise skip)
    # Actually, user provided this token, it might be old.
    # We can check if factory emitted an event for this token in recent blocks?
    # Or just assume it's created by factory.
    
if __name__ == "__main__":
    asyncio.run(analyze_token())
