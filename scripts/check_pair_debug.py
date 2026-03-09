
import asyncio
from web3 import Web3, AsyncWeb3

async def check_pair():
    token_address = "0x3417c3BbDCC6ecF0757aBa83D814B1AB5B6c4444"
    WBNB = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"
    PANCAKE_FACTORY = "0xcA143Ce32Fe78f1f7019d7d551a6402fC5350c73"
    
    rpc_url = "https://bsc-rpc.publicnode.com" # Default
    
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))
    
    factory_abi = [{"constant":True,"inputs":[{"internalType":"address","name":"","type":"address"},{"internalType":"address","name":"","type":"address"}],"name":"getPair","outputs":[{"internalType":"address","name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}]
    
    factory = w3.eth.contract(address=PANCAKE_FACTORY, abi=factory_abi)
    
    print(f"Checking pair for {token_address} on {rpc_url}...")
    
    try:
        pair = await factory.functions.getPair(token_address, WBNB).call()
        print(f"Pair Address: {pair}")
        
        if pair != "0x0000000000000000000000000000000000000000":
            # Check reserves
            pair_abi = [
                {"constant":True,"inputs":[],"name":"getReserves","outputs":[{"internalType":"uint112","name":"_reserve0","type":"uint112"},{"internalType":"uint112","name":"_reserve1","type":"uint112"},{"internalType":"uint32","name":"_blockTimestampLast","type":"uint32"}],"payable":False,"stateMutability":"view","type":"function"},
                {"constant":True,"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"payable":False,"stateMutability":"view","type":"function"}
            ]
            pair_contract = w3.eth.contract(address=pair, abi=pair_abi)
            reserves = await pair_contract.functions.getReserves().call()
            print(f"Reserves: {reserves}")
    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(check_pair())
