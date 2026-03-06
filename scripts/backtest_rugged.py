import asyncio
import os
import sys
import yaml

# Add project root to sys.path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from bsc_bot.analyzer.security_checker import SecurityChecker
from web3 import AsyncWeb3

async def test():
    # Setup simple Web3 (required for LocalSimulator inside SecurityChecker)
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider("https://bsc-rpc.publicnode.com"))
    
    checker = SecurityChecker(w3)
    
    rugged_tokens = [
        "0x3f221Ebd31b6a671d624d5A4F3eb2Caaa270A730",  # 龙虾
        "0x14E003eAaf6874b8Bf9c2Af4e70510857587D21F", # 无尽的
        "0x45722DE93ca96627A832b689dB6E175A38b50b31", # 土拨鼠
    ]
    
    print("\nStarting Backtest on Rugged Tokens...")
    for addr in rugged_tokens:
        # Use a dummy deployer address for backtest if we don't have it
        result = await checker.analyze(addr, deployer_address="0x0000000000000000000000000000000000000000")
        print(f"\n=== {addr[:10]}... ===")
        print(f"最终评分: {result.get('final_score', 'N/A')}")
        print(f"决策: {result.get('decision', 'N/A')}")
        print(f"风险项: {result.get('risk_items', [])}")
        print(f"加分项: {result.get('bonus_items', [])}")
        # print(f"详情: {result}")

if __name__ == "__main__":
    asyncio.run(test())
