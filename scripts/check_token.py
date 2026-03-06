import asyncio
from web3 import AsyncWeb3
from web3.middleware import ExtraDataToPOAMiddleware

TOKEN = "0x37123d4f2f3919f3c6845fe2af70d3d198888dc7"
RPCS = [
    "https://bscrpc.com",
    "https://bsc-dataseed.binance.org",
    "https://1rpc.io/bnb",
    "https://rpc.ankr.com/bsc"
]

ERC20_ABI = [
    {
        "constant": True,
        "inputs": [],
        "name": "decimals",
        "outputs": [{"name": "", "type": "uint8"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    },
    {
        "constant": True,
        "inputs": [],
        "name": "symbol",
        "outputs": [{"name": "", "type": "string"}],
        "payable": False,
        "stateMutability": "view",
        "type": "function"
    }
]

async def check():
    for rpc in RPCS:
        print(f"Trying {rpc}...")
        try:
            w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc, request_kwargs={'timeout': 5}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            
            if await w3.is_connected():
                print("Connected!")
                contract = w3.eth.contract(address=w3.to_checksum_address(TOKEN), abi=ERC20_ABI)
                
                try:
                    decimals = await contract.functions.decimals().call()
                    print(f"✅ Decimals: {decimals}")
                    return
                except Exception as e:
                    print(f"Decimals fetch failed: {e}")
                    # Continue to try other RPCs? No, if connected but failed, likely contract issue.
                    # But maybe rate limit on call?
        except Exception as e:
            print(f"Connection failed: {e}")

if __name__ == "__main__":
    asyncio.run(check())
