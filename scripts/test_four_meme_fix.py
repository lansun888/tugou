"""
验证修复后的 four.meme 监听是否能正确解码 TokenCreate 事件
"""
import asyncio
import os
import sys
os.environ['NO_PROXY'] = 'localhost,127.0.0.1,bsc-dataseed1.binance.org,bsc-rpc.publicnode.com,bsc-dataseed1.defibit.io,1rpc.io,rpc.ankr.com'
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from web3 import AsyncWeb3, Web3
from web3.middleware import ExtraDataToPOAMiddleware
from bsc_bot.monitor.abis import FOUR_MEME_FACTORY_ABI

FACTORY = '0x5c952063c7fc8610FFDB798152D69F0B9550762b'
FOUR_MEME_TOKEN_CREATE_TOPIC = "0x396d5e902b675b032348d3d2e9517ee8f0c4a926603fbc075d3d282ff00cad20"
RPC_URLS = [
    'https://bsc-rpc.publicnode.com',
    'https://bsc-dataseed1.binance.org',
    'https://1rpc.io/bnb',
]

async def main():
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
        print('No RPC'); return

    latest = await w3.eth.block_number
    print(f'Latest block: {latest}')

    contract = w3.eth.contract(address=Web3.to_checksum_address(FACTORY), abi=FOUR_MEME_FACTORY_ABI)

    # Fetch last 200 blocks of TokenCreate events
    logs = await w3.eth.get_logs({
        'fromBlock': latest - 200,
        'toBlock': latest,
        'address': Web3.to_checksum_address(FACTORY),
        'topics': [FOUR_MEME_TOKEN_CREATE_TOPIC]
    })

    print(f'\nFound {len(logs)} TokenCreate events in last 200 blocks')
    if not logs:
        print('No events found - try increasing block range')
        return

    success = 0
    for log in logs[:5]:  # Show first 5
        try:
            event = contract.events.TokenCreate().process_log(log)
            args = event['args']
            print(f'\n  Token:       {args["token"]}')
            print(f'  Creator:     {args["creator"]}')
            print(f'  Name:        {args["name"]}')
            print(f'  Symbol:      {args["symbol"]}')
            print(f'  TotalSupply: {args["totalSupply"]}')
            print(f'  Tx:          {log["transactionHash"].hex()}')
            success += 1
        except Exception as e:
            print(f'  Decode failed: {e}')

    print(f'\n=== Result: {success}/{min(len(logs), 5)} events decoded successfully ===')
    if success > 0:
        print('SUCCESS: four.meme TokenCreate monitoring is working correctly!')
    else:
        print('FAIL: decoding still broken')

asyncio.run(main())
