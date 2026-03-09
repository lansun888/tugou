"""
直接通过RPC查询token创建事件和工厂事件结构
"""
import asyncio
import os
import sys
os.environ['NO_PROXY'] = 'localhost,127.0.0.1,bsc-dataseed1.binance.org,bsc-rpc.publicnode.com,bsc-dataseed1.defibit.io,1rpc.io,rpc.ankr.com,binance.llamarpc.com'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web3 import AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware

TOKEN_ADDR = '0xcf08d70dbc439ad7a4a6af290287f8a00ff84444'
FACTORY = '0x5c952063c7fc8610FFDB798152D69F0B9550762b'
RPC_URLS = [
    'https://bsc-rpc.publicnode.com',
    'https://bsc-dataseed1.binance.org',
    'https://1rpc.io/bnb',
    'https://rpc.ankr.com/bsc',
    'https://binance.llamarpc.com',
]

async def main():
    # Connect to RPC
    w3 = None
    for rpc in RPC_URLS:
        try:
            w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc, request_kwargs={'timeout': 15}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if await w3.is_connected():
                print(f'Connected to {rpc}')
                break
        except Exception as e:
            print(f'Failed {rpc}: {e}')

    if not w3:
        print('No RPC connected'); return

    latest = await w3.eth.block_number
    print(f'Latest block: {latest}')

    # Step 1: Compute topic hashes
    print('\n=== Step 1: Event signature topic hashes ===')
    candidates = [
        "TokenCreate(address,address)",
        "TokenCreate(address,address,uint256,string,string,uint256,uint256,uint256)",
        "TokenCreate(address,address,uint256)",
        "Listed(address,address,uint256)",
        "TokenListed(address,address,uint256)",
        "Graduated(address,address,uint256)",
    ]
    for sig in candidates:
        topic = Web3.keccak(text=sig).hex()
        print(f"  {sig}")
        print(f"    -> 0x{topic}")

    # Step 2: Get any logs from factory (last 3000 blocks) and show all unique topic0s
    print(f'\n=== Step 2: Factory logs (last 3000 blocks) ===')
    from_block = latest - 3000
    try:
        logs = await w3.eth.get_logs({
            'fromBlock': from_block,
            'toBlock': latest,
            'address': Web3.to_checksum_address(FACTORY)
        })
        print(f'Total logs: {len(logs)}')

        topic_map = {}
        for log in logs:
            t0 = '0x' + log['topics'][0].hex()
            if t0 not in topic_map:
                topic_map[t0] = {'count': 0, 'sample': log}
            topic_map[t0]['count'] += 1

        print(f'Unique topic0s: {len(topic_map)}')
        for t0, info in sorted(topic_map.items(), key=lambda x: -x[1]['count']):
            log = info['sample']
            print(f'\n  topic0: {t0}')
            print(f'  count:  {info["count"]}')
            print(f'  ntopics:{len(log["topics"])}')
            print(f'  datalen:{len(log["data"])} bytes')
            print(f'  tx:     {log["transactionHash"].hex()}')
            for i, t in enumerate(log['topics']):
                print(f'  topics[{i}]: 0x{t.hex()}')
            # Show first 128 chars of data
            raw = bytes(log['data'])
            print(f'  data:   {raw.hex()[:128]}...')

    except Exception as e:
        print(f'Error getting factory logs: {e}')

    # Step 3: Try to find the token in any log
    print(f'\n=== Step 3: Search for token {TOKEN_ADDR} in logs ===')
    token_suffix = TOKEN_ADDR.lower()[2:]  # without 0x
    found = False
    for log in logs:
        all_hex = ''.join('0x' + t.hex() for t in log['topics'])
        all_hex += bytes(log['data']).hex()
        if token_suffix in all_hex.lower():
            t0 = '0x' + log['topics'][0].hex()
            print(f'Found token in log! topic0={t0}')
            print(f'  tx: {log["transactionHash"].hex()}')
            print(f'  block: {log["blockNumber"]}')
            for i, t in enumerate(log['topics']):
                print(f'  topics[{i}]: 0x{t.hex()}')
            print(f'  data: {bytes(log["data"]).hex()}')
            found = True
    if not found:
        print(f'Token not found in last 3000 blocks factory logs')
        print(f'(Token may have been created earlier - try scanning more blocks)')

    # Step 4: Get the token contract creation info
    print(f'\n=== Step 4: Token contract code check ===')
    try:
        code = await w3.eth.get_code(Web3.to_checksum_address(TOKEN_ADDR))
        print(f'Token bytecode size: {len(code)} bytes')
        if len(code) == 0:
            print('WARNING: No bytecode! Token address may be wrong.')
    except Exception as e:
        print(f'Error getting token code: {e}')

asyncio.run(main())
