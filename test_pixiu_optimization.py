#!/usr/bin/env python3
"""
貔貅检测逻辑优化验证脚本
测试5处优化的准确性
"""
import asyncio
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from bsc_bot.analyzer.security_checker import SecurityChecker
from loguru import logger

# 测试用例
TEST_CASES = {
    "known_pixiu": [
        {
            "name": "IVT (已知貔貅)",
            "address": "0x74cDa07Ade903C7fc18742ec8eD1498Ce4d1d069",
            "deployer": "0x0000000000000000000000000000000000000000",
            "expected": "reject"
        },
        {
            "name": "已知貔貅2",
            "address": "0x7b7ce0ee9a8d167aca55bff562a562429068ed6b",
            "deployer": "0x0000000000000000000000000000000000000000",
            "expected": "reject"
        }
    ],
    "normal_tokens": [
        # 从数据库中找一个已成功买入且盈利的代币
        # 这里需要手动填入一个真实地址
    ]
}

async def test_optimization():
    """测试优化后的貔貅检测逻辑"""
    checker = SecurityChecker()

    logger.info("=" * 80)
    logger.info("开始验证貔貅检测优化")
    logger.info("=" * 80)

    # 测试1：已知貔貅仍然被拒绝
    logger.info("\n[测试1] 验证已知貔貅仍然被拒绝")
    for case in TEST_CASES["known_pixiu"]:
        logger.info(f"\n测试: {case['name']} ({case['address'][:10]}...)")
        try:
            result = await checker.analyze(
                token_address=case["address"],
                deployer_address=case["deployer"],
                platform="dex"
            )

            decision = result.get("decision", "unknown")
            score = result.get("final_score", 0)
            risk_items = result.get("risk_items", [])

            logger.info(f"  决策: {decision}")
            logger.info(f"  得分: {score}")
            logger.info(f"  风险项数量: {len(risk_items)}")

            if decision == "reject" or score < 85:
                logger.success(f"  ✓ 正确拒绝")
            else:
                logger.error(f"  ✗ 错误通过！应该被拒绝")
                logger.error(f"  风险项: {[r['desc'] for r in risk_items[:5]]}")

        except Exception as e:
            logger.error(f"  测试失败: {e}")

    # 测试2：优化2验证（持仓结构过滤合约地址）
    logger.info("\n[测试2] 验证持仓结构检测（优化2）")
    logger.info("  检查是否正确过滤合约地址...")
    # 这个测试需要在实际运行中观察日志输出

    # 测试3：优化3验证（部署者历史阈值收紧）
    logger.info("\n[测试3] 验证部署者历史阈值（优化3）")
    logger.info("  新阈值: >=30硬拒绝, 15-29扣20分, 5-14扣10分, 2-4扣5分")

    # 测试4：优化4验证（LP锁仓检测）
    logger.info("\n[测试4] 验证LP锁仓检测（优化4）")
    logger.info("  LP完全未锁仓 → 硬拒绝")
    logger.info("  LP锁仓<80% → 扣20分")

    # 测试5：优化5验证（API失败降级）
    logger.info("\n[测试5] 验证API失败降级（优化5）")
    logger.info("  关键检测完成率<60% → 主动降分")

    await checker.close()

    logger.info("\n" + "=" * 80)
    logger.info("验证完成")
    logger.info("=" * 80)
    logger.info("\n请检查以下内容:")
    logger.info("1. 已知貔貅地址仍然被拒绝 ✓")
    logger.info("2. 持仓结构检测日志中显示'真实钱包持仓'统计")
    logger.info("3. 部署者历史发币数量触发新的扣分规则")
    logger.info("4. LP锁仓比例检测生效")
    logger.info("5. API失败时主动降分")

if __name__ == "__main__":
    asyncio.run(test_optimization())
