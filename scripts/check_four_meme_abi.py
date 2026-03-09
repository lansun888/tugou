import aiohttp
import asyncio
import json
from web3 import Web3

BSCSCAN_API_KEY = "Y2WFHBQGP1UXHRARC2IX1NPX11685YRA7W"
CONTRACT_ADDRESS = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
BASE_URL = "https://api.etherscan.io/v2/api"

async def fetch_abi():
    params = {
        "chainid": 56,
        "module": "contract",
        "action": "getabi",
        "address": CONTRACT_ADDRESS,
        "apikey": BSCSCAN_API_KEY
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    async with aiohttp.ClientSession() as session:
        async with session.get(BASE_URL, params=params, headers=headers) as resp:
            data = await resp.json()
            print(f"Response: {data}")
            
            if data["status"] == "1":
                abi_str = data["result"]
                abi = json.loads(abi_str)
                print("✅ ABI fetched successfully")
                return abi
            else:
                print(f"❌ Failed to fetch ABI: {data['message']}")
                return None

def analyze_abi(abi):
    print("\n🔍 Analyzing Events...")
    
    listed_event = None
    token_created_event = None
    
    for item in abi:
        if item["type"] == "event":
            if item["name"] == "Listed":
                listed_event = item
                print(f"\n[Listed Event Found]")
                print(json.dumps(item, indent=2))
                
                # Calculate Topic
                inputs = item["inputs"]
                signature = f"Listed({','.join([i['type'] for i in inputs])})"
                topic = Web3.keccak(text=signature).hex()
                print(f"Signature: {signature}")
                print(f"Topic: {topic}")
                
            elif item["name"] == "TokenCreated":
                token_created_event = item
                print(f"\n[TokenCreated Event Found]")
                print(json.dumps(item, indent=2))
                
                inputs = item["inputs"]
                signature = f"TokenCreated({','.join([i['type'] for i in inputs])})"
                topic = Web3.keccak(text=signature).hex()
                print(f"Signature: {signature}")
                print(f"Topic: {topic}")

if __name__ == "__main__":
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    abi = loop.run_until_complete(fetch_abi())
    if abi:
        analyze_abi(abi)
