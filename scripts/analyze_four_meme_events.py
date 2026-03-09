"""
Step 1: Get first tx of the token via BscScan
Step 2: Get the creation transaction logs
Step 3: Check event topic candidates
"""
import requests
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ['NO_PROXY'] = '*'

from web3 import Web3

token_address = "0xcf08d70dbc439ad7a4a6af290287f8a00ff84444"
BSCSCAN_API_KEY = os.getenv("BSCSCAN_API_KEY", "Y2WFHBQGP1UXHRARC2IX1NPX11685YRA7W")
FACTORY = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"

print("=" * 60)
print("Step 1: BscScan - First TX of token")
print("=" * 60)

url = (
    "https://api.bscscan.com/v2/api"
    "?chainid=56"
    "&module=account"
    "&action=txlist"
    f"&address={token_address}"
    "&startblock=0"
    "&endblock=99999999"
    "&sort=asc"
    "&apikey=" + BSCSCAN_API_KEY
)
resp = requests.get(url, timeout=15).json()
first_tx = resp['result'][0] if resp.get('result') else None
if first_tx:
    print(f"创建者/From:   {first_tx['from']}")
    print(f"创建交易hash: {first_tx['hash']}")
    print(f"区块号:        {first_tx['blockNumber']}")
    print(f"To:            {first_tx['to']}")
    first_tx_hash = first_tx['hash']
    first_block = int(first_tx['blockNumber'])
else:
    print(f"Error: {resp}")
    first_tx_hash = None
    first_block = None

print()
print("=" * 60)
print("Step 2: Get logs for the creation TX")
print("=" * 60)

if first_tx_hash:
    url2 = (
        "https://api.bscscan.com/v2/api"
        "?chainid=56"
        "&module=logs"
        "&action=getLogs"
        f"&txhash={first_tx_hash}"
        "&apikey=" + BSCSCAN_API_KEY
    )
    resp2 = requests.get(url2, timeout=15).json()
    logs = resp2.get('result', [])
    print(f"Found {len(logs)} logs in creation TX")
    for i, log in enumerate(logs):
        print(f"\n  Log[{i}]:")
        print(f"    address:  {log.get('address')}")
        topics = log.get('topics', [])
        for j, t in enumerate(topics):
            print(f"    topic[{j}]: {t}")
        data = log.get('data', '')
        print(f"    data:     {data[:130]}{'...' if len(data) > 130 else ''}")

print()
print("=" * 60)
print("Step 3: Verify event topic signatures")
print("=" * 60)

candidates = [
    "TokenListed(address,address,uint256)",
    "Listed(address,address,uint256)",
    "Launch(address,address)",
    "TokenCreated(address,address,uint256)",
    "Graduated(address,address,uint256)",
    "TokenCreate(address,address)",
    "TokenCreate(address,address,uint256,string,string,uint256,uint256,uint256)",
    "TokenCreate(address,address,uint256)",
]

print(f"\n{'Signature':<60} {'Topic0'}")
print("-" * 110)
for sig in candidates:
    topic = '0x' + Web3.keccak(text=sig).hex()
    print(f"{sig:<60} {topic}")

print()
print("=" * 60)
print("Step 4: Get factory logs around that block")
print("=" * 60)

if first_block:
    url3 = (
        "https://api.bscscan.com/v2/api"
        "?chainid=56"
        "&module=logs"
        "&action=getLogs"
        f"&address={FACTORY}"
        f"&fromBlock={first_block}"
        f"&toBlock={first_block}"
        "&apikey=" + BSCSCAN_API_KEY
    )
    resp3 = requests.get(url3, timeout=15).json()
    factory_logs = resp3.get('result', [])
    print(f"Factory logs in block {first_block}: {len(factory_logs)}")
    for i, log in enumerate(factory_logs):
        print(f"\n  Log[{i}]:")
        print(f"    address: {log.get('address')}")
        topics = log.get('topics', [])
        for j, t in enumerate(topics):
            print(f"    topic[{j}]: {t}")
        data = log.get('data', '')
        print(f"    data: {data[:256]}{'...' if len(data) > 256 else ''}")
        print(f"    tx:   {log.get('transactionHash')}")
