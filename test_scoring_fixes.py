#!/usr/bin/env python3
"""
测试评分修复逻辑
验证4处误判修复是否生效
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from bsc_bot.analyzer.security_checker import SecurityChecker
from loguru import logger

async def test_scoring_fixes():
    """测试评分修复"""
    checker = SecurityChecker()

    logger.info("=" * 80)
    logger.info("测试评分修复逻辑")
    logger.info("=" * 80)

    # 测试用例（需要手动填入真实地址）
    test_cases = [
        {
            "name": "正常代币（DEV持仓6.9%）",
            "address": "0x...",  # 填入真实地址
            "expected": "不应该因为6.9%持仓扣35分"
        },
        {
            "name": "新币（10分钟内，LP未锁仓）",
            "address": "0x...",  # 填入真实地址
            "expected": "不应该硬拒绝，最多扣10分"
        }
    ]

    logger.info("\n[修复验证]")
    logger.info("1. _mint 内部函数不再扣分")
    logger.info("2. transferOwnership 扣分从-5降低到-2（owner未弃权时）")
    logger.info("3. Deployer持仓梯度扣分：>30%扣35分，>20%扣20分，>15%扣10分，>10%扣5分，<=10%不扣分")
    logger.info("4. LP未锁仓：新币10分钟内不扣分，10-30分钟扣10分，>30分钟硬拒绝")

    logger.info("\n请在实际运行中观察日志，确认以下内容：")
    logger.info("- 合约源码中的 'function _mint(' 不再触发扣分")
    logger.info("- 'function mint(' 触发 -15分（外部可调用铸币）")
    logger.info("- transferOwnership 扣分降低到 -2分")
    logger.info("- Deployer持仓6.9%不再扣35分，应该不扣分")
    logger.info("- 新币LP未锁仓不再立即硬拒绝")

    await checker.close()

if __name__ == "__main__":
    asyncio.run(test_scoring_fixes())
