from web3 import Web3
res = Web3.keccak(text='TokenCreate(address,address)').hex()
print(f"TOPIC: {res}")
