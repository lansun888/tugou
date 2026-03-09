import asyncio
from web3 import AsyncWeb3, Web3

RPC_URL = "https://bsc-rpc.publicnode.com"
IMPL_ADDRESS = "0xd63cbf542c7096b1df52c7e97644f365c0ebc6fe"

async def check_bytecode():
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(RPC_URL))
    
    # Checksum address
    impl_address_checksum = Web3.to_checksum_address(IMPL_ADDRESS)
    
    print(f"Fetching bytecode for {impl_address_checksum}...")
    code = await w3.eth.get_code(impl_address_checksum)
    print(f"Code size: {len(code)} bytes")
    
    # Listed(address,address,uint256)
    listed_topic_hex = Web3.keccak(text="Listed(address,address,uint256)").hex()
    listed_topic_bytes = bytes.fromhex(listed_topic_hex.replace("0x", ""))
    
    print(f"Checking for Listed topic: {listed_topic_hex}")
    
    if listed_topic_bytes in code:
        print("✅ Listed event hash FOUND in bytecode!")
    else:
        print("❌ Listed event hash NOT FOUND in bytecode.")
        
    # Check TokenCreated(address,address,uint256) (User's guess)
    token_created_hex = Web3.keccak(text="TokenCreated(address,address,uint256)").hex()
    token_created_bytes = bytes.fromhex(token_created_hex.replace("0x", ""))
    
    print(f"Checking for TokenCreated topic: {token_created_hex}")
    if token_created_bytes in code:
        print("✅ TokenCreated event hash FOUND in bytecode!")
    else:
        print("❌ TokenCreated event hash NOT FOUND in bytecode.")
        
    # Check observed topics
    observed_topics = [
        "7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942",
        "396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20"
    ]
    
    for t in observed_topics:
        t_bytes = bytes.fromhex(t)
        if t_bytes in code:
            print(f"✅ Observed topic {t[:8]}... FOUND in bytecode!")
        else:
            print(f"❌ Observed topic {t[:8]}... NOT FOUND in bytecode.")

    # Check for ASCII strings
    if b"Listed" in code:
        print("✅ ASCII string 'Listed' FOUND in bytecode!")
    else:
        print("❌ ASCII string 'Listed' NOT FOUND in bytecode.")
        
    if b"TokenCreated" in code:
        print("✅ ASCII string 'TokenCreated' FOUND in bytecode!")
    else:
        print("❌ ASCII string 'TokenCreated' NOT FOUND in bytecode.")

if __name__ == "__main__":
    asyncio.run(check_bytecode())
