import unittest
import asyncio
import sys
import os
import json
import time
from unittest.mock import MagicMock, AsyncMock, patch
from dataclasses import asdict

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from executor.position_manager import PositionManager, Position

import tempfile
import shutil

class TestPositionManager(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        # 创建临时目录
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test_bsc_bot.db")

        # Mock Executor
        self.executor = MagicMock()
        self.executor.config = {
            "position_management": {
                "monitor_interval": 1,
                "take_profit": {
                    "levels": [[100, 25], [200, 25]]
                },
                "trailing_stop": {
                    "initial_stop_loss": -50,
                    "pullback_threshold": 40,
                    "levels": [[2.0, 50], [3.0, 150]]
                },
                "time_stop": {
                    "rules": [[6, 20, 50]]
                },
                "daily_risk": {
                    "max_daily_loss": 0.5,
                    "profit_threshold_conservative": 1.0
                }
            }
        }
        self.executor.sell_token = AsyncMock(return_value={"status": "success", "amount_bnb": 0.5})
        self.executor.get_token_price = AsyncMock(return_value={"price_bnb": 1.0})
        
        # 使用临时文件数据库
        self.manager = PositionManager(self.executor, db_path=self.db_path)
        await self.manager.init_manager()
        
        # 重置每日统计 (确保每个测试开始时是干净的)
        self.manager.daily_stats = {
            "date": time.strftime("%Y-%m-%d"),
            "buy_count": 0,
            "profit_bnb": 0.0,
            "loss_bnb": 0.0
        }

    async def asyncTearDown(self):
        # 清理临时目录
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)
        
    async def test_add_position(self):
        """测试添加仓位"""
        res = await self.manager.add_position("0xToken", "TEST", 0.1, 1.0, 100)
        self.assertTrue(res)
        self.assertIn("0xToken", self.manager.positions)
        pos = self.manager.positions["0xToken"]
        self.assertEqual(pos.buy_price_bnb, 0.1)
        self.assertEqual(pos.current_price, 0.1)

    async def test_take_profit_strategy(self):
        """测试分批止盈"""
        res = await self.manager.add_position("0xToken", "TEST", 0.1, 1.0, 100)
        self.assertTrue(res, "添加仓位失败")
        pos = self.manager.positions["0xToken"]
        
        # 1. 价格翻倍 (0.1 -> 0.2, +100%)
        # 设置当前价格
        self.executor.get_token_price.return_value = {"price_bnb": 0.20001} # 稍微多一点确保触发
        
        # 执行一次处理
        await self.manager._process_position("0xToken")
        
        # 验证是否卖出 25%
        self.executor.sell_token.assert_called_with("0xToken", "TEST", 25)
        self.assertEqual(len(pos.sold_portions), 1)
        self.assertEqual(pos.sold_portions[0]["reason"], "tp_100")
        
        # 2. 再次触发不应重复卖出
        self.executor.sell_token.reset_mock()
        await self.manager._process_position("0xToken")
        self.executor.sell_token.assert_not_called()
        
        # 3. 价格三倍 (0.1 -> 0.3, +200%)
        self.executor.get_token_price.return_value = {"price_bnb": 0.30001}
        await self.manager._process_position("0xToken")
        self.executor.sell_token.assert_called_with("0xToken", "TEST", 25)
        self.assertEqual(len(pos.sold_portions), 2)
        self.assertEqual(pos.sold_portions[1]["reason"], "tp_200")

    async def test_trailing_stop_strategy(self):
        """测试追踪止损"""
        # 禁用止盈策略，避免干扰追踪止损测试
        self.manager.config["take_profit"]["levels"] = []
        
        await self.manager.add_position("0xToken", "TEST", 0.1, 1.0, 100)
        pos = self.manager.positions["0xToken"]
        
        # 1. 初始止损 (前30分钟 -50%)
        self.executor.get_token_price.return_value = {"price_bnb": 0.04} # -60%
        await self.manager._process_position("0xToken")
        self.executor.sell_token.assert_called_with("0xToken", "TEST", 100)
        self.assertEqual(pos.sold_portions[0]["reason"], "initial_stop_loss")
        
        # 重置每日统计，防止因上一步亏损导致无法开新仓
        self.manager.daily_stats = {
            "date": time.strftime("%Y-%m-%d"),
            "buy_count": 0,
            "profit_bnb": 0.0,
            "loss_bnb": 0.0
        }
        
        # 重置 mock
        self.executor.sell_token.reset_mock()
        await self.manager.add_position("0xToken2", "TEST2", 0.1, 1.0, 100)
        # 确保仓位添加成功
        self.assertIn("0xToken2", self.manager.positions)
        pos2 = self.manager.positions["0xToken2"]
        
        # 2. 动态止损线 (价格达到 2倍 -> 0.2，止损线移至 +50% -> 0.15)
        # 先把价格拉高到 0.2
        self.executor.get_token_price.return_value = {"price_bnb": 0.2}
        await self.manager._process_position("0xToken2") # 更新 highest_price
        
        # 然后跌到 0.14 (+40%，低于 +50% 止损线)
        self.executor.get_token_price.return_value = {"price_bnb": 0.14}
        await self.manager._process_position("0xToken2")
        
        # 应该触发卖出
        self.executor.sell_token.assert_called()
        args = self.executor.sell_token.call_args[0]
        self.assertEqual(args[2], 100)
        self.assertIn("trailing_stop_profit_50", pos2.sold_portions[0]["reason"])

    async def test_time_stop_strategy(self):
        """测试时间止损"""
        await self.manager.add_position("0xToken", "TEST", 0.1, 1.0, 100)
        pos = self.manager.positions["0xToken"]
        
        # 修改买入时间为 7 小时前
        pos.buy_time = time.time() - 7 * 3600
        
        # 价格涨幅 10% (低于要求的 20%)
        self.executor.get_token_price.return_value = {"price_bnb": 0.11}
        
        await self.manager._process_position("0xToken")
        
        # 应该触发 6h 止损，卖出 50%
        self.executor.sell_token.assert_called_with("0xToken", "TEST", 50)
        self.assertEqual(pos.sold_portions[0]["reason"], "time_stop_6h")

    async def test_daily_risk_control(self):
        """测试每日风控"""
        # 1. 模拟巨额亏损
        self.manager.daily_stats["loss_bnb"] = 1.0 # > max 0.5
        
        can_buy = await self.manager.add_position("0xNew", "NEW", 0.1, 1.0, 100)
        self.assertFalse(can_buy)
        
        # 2. 模拟巨额盈利
        self.manager.daily_stats["loss_bnb"] = 0.0
        self.manager.daily_stats["profit_bnb"] = 2.0 # > threshold 1.0
        
        amount = self.manager.get_suggested_buy_amount(1.0)
        self.assertEqual(amount, 0.5) # 减半

if __name__ == "__main__":
    unittest.main()