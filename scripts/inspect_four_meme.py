"""
Inspect four.meme factory events to understand the actual event structure.
"""
import asyncio
import os
os.environ['NO_PROXY'] = 'localhost,127.0.0.1,bsc-dataseed1.binance.org,bsc-rpc.publicnode.com,bsc-dataseed1.defibit.io,1rpc.io'

import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web3 import AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware
from eth_abi import decode

TOKEN_ADDR = '0xcf08d70dbc439ad7a4a6af290287f8a00ff84444'
FACTORY = '0x5c952063c7fc8610FFDB798152D69F0B9550762b'
RPC_URLS = [
    'https://bsc-dataseed1.binance.org',
    'https://bsc-rpc.publicnode.com',
    'https://1rpc.io/bnb',
    'https://bsc-dataseed1.defibit.io',
]

# Known event signatures to try
KNOWN_TOPICS = {
    '0x396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20': 'TokenCreate(address,address,uint256,string,string,uint256,uint256,uint256)',
    '0xef0c04052959ad172ea72063a1012a3986aa06f24a6f4c41eb46103b9583390c': 'TokenCreate(address,address)',
    '0xdc896958cf16556350a89029fe81166599685cd06d043d64e4d5b3cd4df65d3b': 'Listed(address,address,uint256)',
    '0x7db52723a3b2cdd6164364b3b766e65e540d7be48ffa89582956d8eaebe62942': 'Unknown_0x7db',
}

async def inspect():
    w3 = None
    for rpc in RPC_URLS:
        try:
            w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc, request_kwargs={'timeout': 10}))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
            if await w3.is_connected():
                print(f'Connected to {rpc}')
                break
        except Exception as e:
            print(f'Failed {rpc}: {e}')

    if not w3:
        print('No RPC connected')
        return

    latest = await w3.eth.block_number
    print(f'Latest block: {latest}')

    # Get all logs from factory (last 5000 blocks)
    from_block = latest - 5000
    logs = await w3.eth.get_logs({
        'fromBlock': from_block,
        'toBlock': latest,
        'address': FACTORY
    })
    print(f'Found {len(logs)} logs from factory (last 5000 blocks)\n')

    # Analyze unique topic0s
    topics = {}
    for log in logs:
        t0 = log['topics'][0].hex()
        if not t0.startswith('0x'):
            t0 = '0x' + t0
        if t0 not in topics:
            topics[t0] = {'count': 0, 'sample': log}
        topics[t0]['count'] += 1

    print('=== Unique topic0s ===')
    for t0, info in topics.items():
        name = KNOWN_TOPICS.get(t0, 'UNKNOWN')
        log = info['sample']
        print(f'  topic0: {t0}')
        print(f'    name:      {name}')
        print(f'    count:     {info["count"]}')
        print(f'    ntopics:   {len(log["topics"])}')
        print(f'    datalen:   {len(log["data"])} bytes')
        print(f'    tx:        {log["transactionHash"].hex()}')

        # Print all topics
        for i, t in enumerate(log['topics']):
            th = t.hex()
            if not th.startswith('0x'): th = '0x' + th
            print(f'    topics[{i}]: {th}')

        # Try to decode data as various types
        raw = bytes(log['data'])
        if len(raw) > 0:
            print(f'    data_hex:  {raw.hex()[:128]}...' if len(raw) > 64 else f'    data_hex:  {raw.hex()}')
        print()

    # Now specifically look for the token creation tx of 0xcf08...
    print(f'\n=== Looking for token {TOKEN_ADDR} ===')
    token_lower = TOKEN_ADDR.lower()
    for log in logs:
        # Check if token address appears in any topic or data
        log_data = bytes(log['data']).hex()
        topics_hex = ''.join(t.hex() for t in log['topics'])
        combined = topics_hex + log_data
        if token_lower[2:] in combined.lower():
            t0 = log['topics'][0].hex()
            if not t0.startswith('0x'): t0 = '0x' + t0
            name = KNOWN_TOPICS.get(t0, 'UNKNOWN')
            print(f'Found! topic0={t0} ({name}), tx={log["transactionHash"].hex()}')
            print(f'  ntopics: {len(log["topics"])}')
            for i, t in enumerate(log['topics']):
                th = t.hex()
                if not th.startswith('0x'): th = '0x' + th
                print(f'  topics[{i}]: {th}')
            print(f'  data: {bytes(log["data"]).hex()}')

if __name__ == '__main__':
    asyncio.run(inspect())
