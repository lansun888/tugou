"""
测试貔貅检测逻辑
验证新增的 check_price_sanity 和 check_sell_feasibility 函数
"""
import asyncio
import sys
import os

# 添加项目路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from web3 import AsyncWeb3
from bsc_bot.analyzer.security_checker import SecurityChecker
from bsc_bot.analyzer.local_simulator import LocalSimulator

# 测试地址（已知貔貅币）
TEST_ADDRESSES = [
    "0x78b51e10b3B8defEa2c639d41",  # SUP (单价1513 BNB)
    "0x6B9B690D58D42aDF3CFBdb",     # MC (SELL revert)
    "0x04039C52c9793b7393Db659b",  # JGGL (单价1012 BNB)
    "0x74cDa07Ade903C7fc18742ec8", # IVT (上次漏检)
]

async def test_detection():
    """测试貔貅检测"""
    # 初始化 Web3
    rpc_url = "https://bsc-dataseed1.binance.org"
    w3 = AsyncWeb3(AsyncWeb3.AsyncHTTPProvider(rpc_url))

    # 初始化检测器
    simulator = LocalSimulator(w3)
    checker = SecurityChecker(w3, simulator)

    print("=" * 80)
    print("貔貅检测测试")
    print("=" * 80)

    for i, addr in enumerate(TEST_ADDRESSES, 1):
        print(f"\n[{i}/{len(TEST_ADDRESSES)}] 测试地址: {addr}")
        print("-" * 80)

        try:
            # 测试价格异常检测
            print("1. 价格异常检测...")
            price_result = await checker.check_price_sanity(addr)
            if price_result.get("reject"):
                print(f"   ✓ 拦截成功: {price_result.get('reason')}")
                continue
            else:
                print(f"   ✗ 未拦截 (price_sanity)")

            # 测试卖出可行性检测
            print("2. 卖出可行性检测...")
            sell_result = await checker.check_sell_feasibility(addr)
            if sell_result.get("reject"):
                print(f"   ✓ 拦截成功: {sell_result.get('reason')}")
                continue
            else:
                print(f"   ✗ 未拦截 (sell_feasibility)")

            print(f"   ⚠️  警告: 该地址未被任何检测拦截!")

        except Exception as e:
            print(f"   ✗ 检测失败: {e}")
            import traceback
            traceback.print_exc()

    print("\n" + "=" * 80)
    print("测试完成")
    print("=" * 80)

if __name__ == "__main__":
    asyncio.run(test_detection())
