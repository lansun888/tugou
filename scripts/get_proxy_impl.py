import asyncio
from web3 import AsyncWeb3

RPC_URL = "https://bsc-rpc.publicnode.com"
CONTRACT_ADDRESS = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"
# EIP-1967 Implementation slot
IMPLEMENTATION_SLOT = "0x360894a13ba1a3210667c828492db98dca3e2076cc3735a920a3ca505d382bbc"

async def get_implementation():
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(RPC_URL))
    
    print(f"Reading storage at {IMPLEMENTATION_SLOT}...")
    impl_hex = await w3.eth.get_storage_at(CONTRACT_ADDRESS, IMPLEMENTATION_SLOT)
    
    print(f"Raw storage: {impl_hex.hex()}")
    
    # Convert to address (last 20 bytes)
    impl_address = "0x" + impl_hex.hex()[-40:]
    print(f"Implementation Address: {impl_address}")
    
    return impl_address

if __name__ == "__main__":
    asyncio.run(get_implementation())
