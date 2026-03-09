from web3 import Web3

sig = "CreateToken(address,address,uint256,uint256,uint256,uint256,uint256,uint256)"
print(f"{sig}: {Web3.keccak(text=sig).hex()}")

sig2 = "TokenLaunched(address,address,uint256,uint256,uint256,uint256,uint256,uint256)"
print(f"{sig2}: {Web3.keccak(text=sig2).hex()}")

sig3 = "Launch(address,address,uint256,uint256,uint256,uint256,uint256,uint256)"
print(f"{sig3}: {Web3.keccak(text=sig3).hex()}")

