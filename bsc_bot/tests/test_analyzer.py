import unittest
import asyncio
import sys
import os
from unittest.mock import MagicMock, AsyncMock, patch
import json

# 添加项目根目录到路径
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from analyzer.security_checker import SecurityChecker

class TestSecurityChecker(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.checker = SecurityChecker()
        self.checker.session = MagicMock()
        
    async def asyncTearDown(self):
        await self.checker.close()

    async def test_load_blacklist(self):
        """测试黑名单加载"""
        blacklist = self.checker._load_blacklist()
        self.assertTrue(len(blacklist) > 0)
        self.assertIn("0xbad1234567890123456789012345678901234567", blacklist)

    @patch('analyzer.security_checker.SecurityChecker._get_session')
    async def test_goplus_api(self, mock_get_session):
        """测试 GoPlus API 解析"""
        # 模拟 GoPlus 响应
        mock_resp = AsyncMock()
        mock_resp.status = 200
        mock_resp.json.return_value = {
            "result": {
                "0xtesttoken": {
                    "is_honeypot": "1", # 蜜罐
                    "buy_tax": "0.1",
                    "sell_tax": "0.1"
                }
            }
        }
        
        # 模拟 session 对象
        mock_session = AsyncMock()
        
        # 关键修复：AsyncMock 自动创建的子属性（如 session.get）默认也是 AsyncMock。
        # 当调用 session.get() 时，它返回一个协程。
        # 我们需要它返回一个对象，该对象实现了 __aenter__ 和 __aexit__。
        # 但在 `async with session.get(...)` 中，session.get(...) 被调用。
        # 如果 session.get 是 AsyncMock，调用它返回一个 Coroutine。
        # 这个 Coroutine 被 await 吗？不，`async with EXPR` 中，EXPR 必须有一个 __aenter__ 方法。
        # 如果 EXPR 是一个协程对象，这通常是不行的，除非它也是一个异步上下文管理器。
        # aiohttp.ClientSession.get 返回的是一个 _RequestContextManager，它不是协程，但它的 __aenter__ 是异步的。
        
        # 解决方案：让 session.get 是一个 MagicMock，而不是 AsyncMock。
        # 这样调用 session.get() 就返回我们指定的上下文管理器对象，而不是一个协程。
        mock_session.get = MagicMock()
        
        mock_get_context = MagicMock()
        mock_get_context.__aenter__.return_value = mock_resp
        mock_get_context.__aexit__.return_value = None
        
        mock_session.get.return_value = mock_get_context
        
        # 确保 _get_session 返回这个 mock_session
        mock_get_session.return_value = mock_session

        result = await self.checker.check_goplus("0xTestToken")
        self.assertEqual(result["is_honeypot"], "1")
        self.assertEqual(result["buy_tax"], "0.1")

    @patch('analyzer.security_checker.SecurityChecker.check_goplus')
    @patch('analyzer.security_checker.SecurityChecker.check_honeypot_is')
    @patch('analyzer.security_checker.SecurityChecker.check_contract_code')
    async def test_analyze_logic(self, mock_contract, mock_honeypot, mock_goplus):
        """测试综合分析逻辑"""
        
        # 模拟场景：非蜜罐，但税率高，且未开源
        mock_goplus.return_value = {
            "is_honeypot": "0",
            "buy_tax": "0.3", # 30% 税率 -> 扣分
            "sell_tax": "0.3",
            "is_mintable": "0"
        }
        mock_honeypot.return_value = {"simulationSuccess": True}
        mock_contract.return_value = {"SourceCode": ""} # 未开源 -> 扣分

        result = await self.checker.analyze("0xTestToken", "0xDeployer")
        
        # 验证扣分项
        # 初始 100
        # 税率 > 25% -> -30
        # 未开源 -> -20
        # 预期分数 <= 50
        
        self.assertTrue(result["final_score"] <= 50)
        self.assertEqual(result["decision"], "notify") # 40-59 分

    @patch('analyzer.security_checker.SecurityChecker.check_goplus')
    @patch('analyzer.security_checker.SecurityChecker.check_honeypot_is')
    @patch('analyzer.security_checker.SecurityChecker.check_contract_code')
    async def test_safe_token(self, mock_contract, mock_honeypot, mock_goplus):
        """测试安全代币场景"""
        
        mock_goplus.return_value = {
            "is_honeypot": "0",
            "buy_tax": "0.01",
            "sell_tax": "0.01",
            "is_mintable": "0",
            "is_proxy": "0"
        }
        mock_honeypot.return_value = {"simulationSuccess": True}
        # 模拟开源且包含 owner = address(0) (放弃所有权)
        mock_contract.return_value = {"SourceCode": "contract Token { ... owner = address(0); ... }"}

        result = await self.checker.analyze("0xSafeToken", "0xGoodDeployer")
        
        # 初始 100
        # 开源 +10
        # 放弃所有权 +10
        # 预期分数 >= 100 (上限 100)
        
        self.assertEqual(result["final_score"], 100)
        self.assertEqual(result["decision"], "buy")

if __name__ == "__main__":
    unittest.main()
