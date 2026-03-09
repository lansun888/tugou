from web3 import Web3

def check_event_hash(signature):
    hash_val = Web3.keccak(text=signature).hex()
    print(f"{signature}: {hash_val}")

signatures = [
    "TokenCreate(address,address)",
    "TokenCreated(address,address)",
    "CreateToken(address,address)",
    "Listed(address,address,uint256)",
    "PairCreated(address,address,address,uint256)",
    "PoolCreated(address,address,uint24,int24,address)",
    "Transfer(address,address,uint256)",
    "Approval(address,address,uint256)",
    "OwnershipTransferred(address,address)",
    "TokenCreate(address,address,string,string)",
    "TokenCreate(address,address,string,string,uint256)",
    "Listed(address,address,uint256,uint256)",
    "Trade(address,address,uint256,uint256,uint256,uint256)",
    "Swap(address,uint256,uint256,uint256,uint256,address)",
    "Mint(address,uint256,uint256)",
    "Burn(address,uint256,uint256,address)",
]

print("Calculating hashes...")
for sig in signatures:
    check_event_hash(sig)

print("\nTarget Hashes from logs:")
print("0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942")
print("0x48063b1239b68b5d50123408787a6df1f644d9160f0e5f702fefddb9a855954d")
